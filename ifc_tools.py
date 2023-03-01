#***************************************************************************
#*                                                                         *
#*   Copyright (c) 2022 Yorik van Havre <yorik@uncreated.net>              *
#*                                                                         *
#*   This program is free software; you can redistribute it and/or modify  *
#*   it under the terms of the GNU General Public License (GPL)            *
#*   as published by the Free Software Foundation; either version 3 of     *
#*   the License, or (at your option) any later version.                   *
#*   for detail see the LICENCE text file.                                 *
#*                                                                         *
#*   This program is distributed in the hope that it will be useful,       *
#*   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#*   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#*   GNU General Public License for more details.                          *
#*                                                                         *
#*   You should have received a copy of the GNU Library General Public     *
#*   License along with this program; if not, write to the Free Software   *
#*   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
#*   USA                                                                   *
#*                                                                         *
#***************************************************************************

import os
import multiprocessing

import FreeCAD
from FreeCAD import Base
import Part
import Mesh
from pivy import coin

import ifcopenshell
from ifcopenshell import geom
from ifcopenshell import api
from ifcopenshell.util import attribute
from ifcopenshell.util import schema

import ifc_objects
import ifc_viewproviders

SCALE = 1000.0 # IfcOpenShell works in meters, FreeCAD works in mm

def create_document(filename, document, shapemode=0, strategy=0):

    """Creates a FreeCAD IFC document object.
    shapemode: 0 = full shape
               1 = coin only
    strategy:  0 = only root object
               1 = only bbuilding structure,
               2 = all children
    """

    obj = add_object(document, fctype='document')
    d = "The path to the linked IFC file"
    obj.addProperty("App::PropertyFile","FilePath","Base",d)
    obj.addProperty("App::PropertyBool","Modified","Base")
    obj.setPropertyStatus("Modified","Hidden")
    obj.FilePath = filename
    ifcfile = ifcopenshell.open(filename)
    obj.Proxy.ifcfile = ifcfile
    project = ifcfile.by_type("IfcProject")[0]
    add_properties(project, obj, ifcfile, holdshape=not(bool(shapemode)))
    # populate according to strategy
    if strategy == 0:
        pass
    elif strategy == 1:
        create_children(obj, ifcfile, recursive=True, only_structure=True)
    elif strategy == 2:
        create_children(obj, ifcfile, recursive=True, assemblies=False)
    return obj


def create_object(ifcentity, document, ifcfile, holdshape=False):

    """Creates a FreeCAD object from an IFC entity"""

    print("#{}: {}, '{}'".format(ifcentity.id(), ifcentity.is_a(), ifcentity.Name))
    obj = add_object(document)
    add_properties(ifcentity, obj, ifcfile, holdshape=holdshape)
    elements = [ifcentity]
    return obj


def create_children(obj, ifcfile, recursive=False, only_structure=False, assemblies=True):

    """Creates a hierarchy of objects under an object"""

    def create_child(parent, element):
        subresult = []
        # do not create if a child with same stepid already exists
        if not element.id() in [getattr(c,"StepId",0) for c in getattr(parent,"Group",[])]:
            child = create_object(element, parent.Document, ifcfile, parent.HoldShape)
            subresult.append(child)
            parent.addObject(child)
            if element.is_a("IfcSite"):
                # force-create contained buildings too if we just created a site
                buildings = [o for o in get_children(child, ifcfile) if o.is_a("IfcBuilding")]
                for building in buildings:
                    subresult.extend(create_child(child, building))
            if recursive:
                subresult.extend(create_children(child, ifcfile, recursive, only_structure, assemblies))
        return subresult

    result = []
    for child in get_children(obj, ifcfile, only_structure, assemblies):
        result.extend(create_child(obj, child))
    return result


def get_children(obj, ifcfile, only_structure=False, assemblies=True):

    """Returns the direct descendants of an object"""

    ifcentity = ifcfile[obj.StepId]
    children = []
    if assemblies or not ifcentity.is_a("IfcElement"):
        for rel in getattr(ifcentity, "IsDecomposedBy", []):
            children.extend(rel.RelatedObjects)
    if not only_structure:
        for rel in getattr(ifcentity, "ContainsElements", []):
            children.extend(rel.RelatedElements)
        for rel in getattr(ifcentity, "HasOpenings", []):
            children.extend([rel.RelatedOpeningElement])
        for rel in getattr(ifcentity, "HasFillings", []):
            children.extend([rel.RelatedBuildingElement])
    return filter_elements(children, ifcfile, expand=False)


def get_ifcfile(obj):

    """Returns the ifcfile that handles this object"""

    project = get_project(obj)
    if project:
        if hasattr(project,"Proxy"):
            if hasattr(project.Proxy,"ifcfile"):
                return project.Proxy.ifcfile
        if project.FilePath:
            ifcfile = ifcopenshell.open(project.FilePath)
            if hasattr(project,"Proxy"):
                project.Proxy.ifcfile = ifcfile
            return ifcfile
    return None


def get_project(obj):

    """Returns the ifcdocument this object belongs to"""

    proj_types = ("IfcProject","IfcProjectLibrary")
    if getattr(obj, "Type", None) in proj_types:
        return obj
    if hasattr(obj,"InListRecursive"):
        for parent in obj.InListRecursive:
            if getattr(parent, "Type", None) in proj_types:
                return parent
    return None


def can_expand(obj, ifcfile):

    """Returns True if this object can have any more child extracted"""

    children = get_children(obj, ifcfile)
    group = [o.StepId for o in obj.Group]
    for child in children:
        if child.id() not in group:
            return True
    return False


def add_object(document, fctype="object"):

    """adds a new object to a FreeCAD document"""

    otype = 'Part::FeaturePython'
    ot = ifc_objects.ifc_object()
    if fctype == "document":
        vp = ifc_viewproviders.ifc_vp_document()
    else:
        vp = ifc_viewproviders.ifc_vp_object()
    obj = document.addObject(otype, 'IfcObject', ot, vp, False)
    return obj


def add_properties(ifcentity, obj, ifcfile, links=False, holdshape=False):

    """Adds the properties of the given IFC object to a FreeCAD object"""

    if getattr(ifcentity, "Name", None):
        obj.Label = ifcentity.Name
    else:
        obj.Label = ifcentity.is_a()
    obj.addExtension('App::GroupExtensionPython')
    if FreeCAD.GuiUp:
        obj.ViewObject.addExtension("Gui::ViewProviderGroupExtensionPython")
    obj.addProperty("App::PropertyBool", "HoldShape", "Base")
    obj.HoldShape = holdshape
    attr_defs = ifcentity.wrapped_data.declaration().as_entity().all_attributes()
    info_ifcentity = get_elem_attribs(ifcentity)
    for attr, value in info_ifcentity.items():
        if attr == "type":
            attr = "Type"
        elif attr == "id":
            attr = "StepId"
        elif attr == "Name":
            continue
        attr_def = next((a for a in attr_defs if a.name() == attr), None)
        data_type = ifcopenshell.util.attribute.get_primitive_type(attr_def) if attr_def else None
        if attr not in obj.PropertiesList:
            if attr == "Type":
                # main enum property, not saved to file
                obj.addProperty("App::PropertyEnumeration", attr, "IFC")
                obj.setPropertyStatus(attr,"Transient")
                setattr(obj, attr, get_ifc_classes(obj, value))
                setattr(obj, attr, value)
                # companion hidden propertym that gets saved to file
                obj.addProperty("App::PropertyString", "IfcType", "IFC")
                obj.setPropertyStatus("IfcType","Hidden")
                setattr(obj, "IfcType", value)
            elif isinstance(value, int):
                obj.addProperty("App::PropertyInteger", attr, "IFC")
                setattr(obj, attr, value)
                if attr == "StepId":
                    obj.setPropertyStatus(attr,"ReadOnly")
            elif isinstance(value, float):
                obj.addProperty("App::PropertyFloat", attr, "IFC")
                setattr(obj, attr, value)
            elif data_type == "boolean":
                obj.addProperty("App::PropertyBool", attr, "IFC")
                setattr(obj, attr, value) #will trigger error. TODO: Fix this
            elif isinstance(value, ifcopenshell.entity_instance):
                if links:
                    #value = create_object(value, obj.Document)
                    obj.addProperty("App::PropertyLink", attr, "IFC")
                    #setattr(obj, attr, value)
            elif isinstance(value, (list, tuple)) and value:
                if isinstance(value[0], ifcopenshell.entity_instance):
                    if links:
                        #nvalue = []
                        #for elt in value:
                        #    nvalue.append(create_object(elt, obj.Document))
                        obj.addProperty("App::PropertyLinkList", attr, "IFC")
                        #setattr(obj, attr, nvalue)
            elif data_type == "enum":
                obj.addProperty("App::PropertyEnumeration", attr, "IFC")
                items = ifcopenshell.util.attribute.get_enum_items(attr_def)
                setattr(obj, attr, items)
                if not value in items:
                    for v in ("UNDEFINED","NOTDEFINED","USERDEFINED"):
                        if v in items:
                            value = v
                            break
                if value in items:
                    setattr(obj, attr, value)
            else:
                obj.addProperty("App::PropertyString", attr, "IFC")
                if value is not None:
                    setattr(obj, attr, str(value))


def get_ifc_classes(obj, baseclass):

    """Returns a list of sibling classes from a given FreeCAD object"""

    if baseclass in ("IfcProject","IfcProjectLibrary"):
        return ("IfcProject","IfcProjectLibrary")
    ifcfile = get_ifcfile(obj)
    if not ifcfile:
        return [baseclass]
    schema = ifcfile.wrapped_data.schema_name()
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(schema)
    declaration = schema.declaration_by_name(baseclass)
    if "StandardCase" in baseclass:
        declaration = declaration.supertype()
    classes = [sub.name() for sub in declaration.supertype().subtypes()]
    # also include subtypes of the current class (ex, StandardCases)
    classes.extend([sub.name() for sub in declaration.subtypes()])
    if not baseclass in classes:
        classes.append(baseclass)
    return classes


def get_ifc_element(obj):

    """Returns the corresponding IFC element of an object"""

    ifc_file = get_ifcfile(obj)
    if ifc_file and hasattr(obj, "StepId"):
        return ifc_file.by_id(obj.StepId)
    return None


def has_representation(element):

    """Tells if an elements has an own representation"""

    if hasattr(element,"Representation") and element.Representation:
        return True
    return False


def filter_elements(elements, ifcfile, expand=True):

    """Filter elements list of unwanted types"""

    # gather decomposition if needed
    if expand and (len(elements) == 1):
        if not has_representation(elements[0]):
            if elements[0].is_a("IfcProject"):
                elements = ifcfile.by_type("IfcElement")
                elements.extend(ifcfile.by_type("IfcSite"))
            else:
                elements = ifcopenshell.util.element.get_decomposition(elements[0])
    # Never load feature elements, they can be lazy loaded
    elements = [e for e in elements if not e.is_a("IfcFeatureElement")]
    # do not load spaces for now (TODO handle them correctly)
    elements = [e for e in elements if not e.is_a("IfcSpace")]
    # skip projects
    elements = [e for e in elements if not e.is_a("IfcProject")]
    # skip furniture for now, they can be lazy loaded probably
    elements = [e for e in elements if not e.is_a("IfcFurnishingElement")]
    # skip annotations for now
    elements = [e for e in elements if not e.is_a("IfcAnnotation")]
    return elements


def get_cache(ifcfile):

    """Returns the shape cache dictionary associated with this ifc file"""

    for d in FreeCAD.listDocuments().values():
        for o in d.Objects:
            if hasattr(o,"Proxy") and hasattr(o.Proxy,"ifcfile"):
                if o.Proxy.ifcfile == ifcfile:
                    if hasattr(o.Proxy,"ifccache") and o.Proxy.ifccache:
                        return o.Proxy.ifccache
    return {"Shape":{},"Color":{},"Coin":{}}


def set_cache(ifcfile, cache):

    """Sets the given dictionary as shape cache for the given ifc file"""

    for d in FreeCAD.listDocuments().values():
        for o in d.Objects:
            if hasattr(o,"Proxy") and hasattr(o.Proxy,"ifcfile"):
                if o.Proxy.ifcfile == ifcfile:
                    o.Proxy.ifccache = cache
                    return


def get_shape(elements, ifcfile, cached=False):

    """Returns a Part shape from a list of IFC entities"""

    elements = filter_elements(elements, ifcfile)
    if len(elements) == 0:
        return None, None  # happens on empty storeys
    shapes = []
    colors = []
    # process cached elements
    cache = get_cache(ifcfile)
    if cached:
        rest = []
        for e in elements:
            if e.id in cache["Shape"]:
                s = cache["Shape"][e.id]
                shapes.append(s.copy())
                if e.id in cache["Color"]:
                    c = cache["Color"][e.id]
                else:
                    c = (0.8,0.8,0.8)
                for f in s.Faces:
                    colors.append(c)
            else:
                rest.append(e)
        elements = rest
    if not elements:
        return shapes, colors
    progressbar = Base.ProgressIndicator()
    total = len(elements)
    progressbar.start("Generating "+str(total)+" shapes...",total)
    settings = get_settings(ifcfile)
    cores = multiprocessing.cpu_count()
    iterator = ifcopenshell.geom.iterator(settings, ifcfile, cores, include=elements)
    is_valid = iterator.initialize()
    if not is_valid:
        print("DEBUG: ifc_tools.get_shape: Invalid iterator")
        return None, None
    while True:
        item = iterator.get()
        if item:
            brep = item.geometry.brep_data
            shape = Part.Shape()
            shape.importBrepFromString(brep, False)
            mat = get_matrix(item.transformation.matrix.data)
            shape.scale(SCALE)
            shape.transformShape(mat)
            shapes.append(shape)
            color = item.geometry.surface_styles
            #color = (color[0], color[1], color[2], 1.0 - color[3])
            # TODO temp workaround for tranparency bug
            color = (color[0], color[1], color[2], 0.0)
            for f in shape.Faces:
                colors.append(color)
            cache["Shape"][item.id]=shape
            cache["Color"][item.id]=color
            progressbar.next(True)
        if not iterator.next():
            break
    set_cache(ifcfile, cache)
    if len(shapes) == 1:
        shape = shapes[0]
    else:
        shape = Part.makeCompound(shapes)
    progressbar.stop()
    return shape, colors


def get_coin(elements, ifcfile, cached=False):

    """Returns a Coin node from a list of IFC entities"""

    elements = filter_elements(elements, ifcfile)
    nodes = coin.SoSeparator()
    # process cached elements
    cache = get_cache(ifcfile)
    if cached:
        rest = []
        for e in elements:
            if e.id() in cache["Coin"]:
                nodes.addChild(cache["Coin"][e.id()].copy())
            else:
                rest.append(e)
        elements = rest
    elements = [e for e in elements if has_representation(e)]
    if not elements:
        return nodes, None
    if nodes.getNumChildren():
        print("DEBUG: The following elements are excluded because they make coin crash (need to investigate):")
        print("DEBUG: If you wish to test, comment out line 488 (return nodes, None) in ifc_tools.py")
        [print("   ", e) for e in elements]
        return nodes, None
    progressbar = Base.ProgressIndicator()
    total = len(elements)
    progressbar.start("Generating "+str(total)+" shapes...",total)
    settings = get_settings(ifcfile, brep=False)
    cores = multiprocessing.cpu_count()
    iterator = ifcopenshell.geom.iterator(settings, ifcfile, cores, include=elements)
    is_valid = iterator.initialize()
    if not is_valid:
        print("DEBUG: ifc_tools.get_coin: Invalid iterator")
        return None, None
    while True:
        item = iterator.get()
        if item:
            node = coin.SoSeparator()
            # colors
            if item.geometry.materials:
                color = item.geometry.materials[0].diffuse
                color = (color[0], color[1], color[2], 0.0)
                mat = coin.SoMaterial()
                mat.diffuseColor.setValue(color[:3])
                # TODO treat transparency
                #mat.transparency.setValue(0.8)
                node.addChild(mat)
            # verts
            matrix = get_matrix(item.transformation.matrix.data)
            verts = item.geometry.verts
            verts = [FreeCAD.Vector(verts[i:i+3]) for i in range(0,len(verts),3)]
            verts = [tuple(matrix.multVec(v.multiply(SCALE))) for v  in verts]
            coords = coin.SoCoordinate3()
            coords.point.setValues(verts)
            node.addChild(coords)
            # faces
            faces = list(item.geometry.faces)
            faces = [f for i in range(0,len(faces),3) for f in faces[i:i+3]+[-1]]
            faceset = coin.SoIndexedFaceSet()
            faceset.coordIndex.setValues(faces)
            node.addChild(faceset)
            nodes.addChild(node)
            cache["Coin"][item.id] = node
            progressbar.next(True)
        if not iterator.next():
            break
    set_cache(ifcfile, cache)
    progressbar.stop()
    return nodes, None


def get_settings(ifcfile, brep=True):

    """Returns ifcopenshell settings"""

    settings = ifcopenshell.geom.settings()
    if brep:
        settings.set(settings.DISABLE_TRIANGULATION, True)
        settings.set(settings.USE_BREP_DATA,True)
        settings.set(settings.SEW_SHELLS,True)
    body_contexts = get_body_context_ids(ifcfile)
    if body_contexts:
        settings.set_context_ids(body_contexts)
    return settings


def set_geometry(obj, elem, ifcfile, cached=False):

    """Sets the geometry of the given object
    obj: FreeCAD document object
    elem: IfcOpenShell ifc entity instance
    ifcfile: IfcOpenShell ifc file instance
    """

    if not obj or not elem or not ifcfile:
        return
    basenode = None
    colors = None
    if obj.ViewObject:
        # getChild(2) is master on/off switch,
        # getChild(0) is flatlines display mode (1 = shaded, 2 = wireframe, 3 = points)
        basenode = obj.ViewObject.RootNode.getChild(2).getChild(0)
        if basenode.getNumChildren() == 5:
            # Part VP has 4 nodes, we have added 1 more
            basenode.removeChild(4)
    if obj.Group and not(has_representation(get_ifc_element(obj))):
        # workaround for group extension bug: add a dummy placeholder shape)
        # otherwise a shape is force-created from the child shapes
        # and we don't want that otherwise we can't select children
        obj.Shape = Part.makeBox(1,1,1)
        colors = None
    elif obj.HoldShape:
        # set object shape
        shape, colors = get_shape([elem], ifcfile, cached)
        if shape is None:
            print(
                "Debug: No Shape returned for FC-IfcObject: {}, {}, {}"
                .format(obj.StepId, obj.IfcType, obj.Label)
            )
        else:
            placement = shape.Placement
            obj.Shape = shape
            obj.Placement = placement
    elif basenode:
        if obj.Group:
            # this is for objects that have own coin representation,
            # but shapes among their children and not taken by first if
            # case above. TODO do this more elegantly
            obj.Shape = Part.makeBox(1,1,1)
        # set coin representation
        node, colors = get_coin([elem], ifcfile, cached)
        basenode.addChild(node)
    set_colors(obj, colors)


def set_attribute(ifcfile, element, attribute, value):

    """Sets the value of an attribute of an IFC element"""

    if attribute == "Type":
        if value != element.is_a():
            if value and value.startswith("Ifc"):
                cmd = 'root.reassign_class'
                FreeCAD.Console.PrintLog("Changing IFC class value: "+element.is_a()+" to "+str(value)+"\n")
                product = ifcopenshell.api.run(cmd, ifcfile, product=element, ifc_class=value)
                # TODO fix attributes
                return product
    cmd = 'attribute.edit_attributes'
    attribs = {attribute: value}
    if hasattr(element, attribute):
        if getattr(element, attribute) != value:
            FreeCAD.Console.PrintLog("Changing IFC attribute value of "+str(attribute)+": "+str(value)+"\n")
            ifcopenshell.api.run(cmd, ifcfile, product=element, attributes=attribs)
            return True
    return False


def set_colors(obj, colors):

    """Sets the given colors to an object"""

    if FreeCAD.GuiUp and colors:
        if hasattr(obj.ViewObject,"ShapeColor"):
            obj.ViewObject.ShapeColor = colors[0][:3]
        if hasattr(obj.ViewObject,"DiffuseColor"):
            obj.ViewObject.DiffuseColor = colors


def get_body_context_ids(ifcfile):

    # Facetation is to accommodate broken Revit files
    # See https://forums.buildingsmart.org/t/suggestions-on-how-to-improve-clarity-of-representation-context-usage-in-documentation/3663/6?u=moult
    body_contexts = [
        c.id()
        for c in ifcfile.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier in ["Body", "Facetation"]
    ]
    # Ideally, all representations should be in a subcontext, but some BIM programs don't do this correctly
    body_contexts.extend(
        [
            c.id()
            for c in ifcfile.by_type("IfcGeometricRepresentationContext", include_subtypes=False)
            if c.ContextType == "Model"
        ]
    )
    return body_contexts


def get_plan_contexts_ids(ifcfile):

    # Annotation is to accommodate broken Revit files
    # See https://github.com/Autodesk/revit-ifc/issues/187
    return [
        c.id()
        for c in ifcfile.by_type("IfcGeometricRepresentationContext")
        if c.ContextType in ["Plan", "Annotation"]
    ]


def get_matrix(ios_matrix):

    """Converts an IfcOpenShell matrix tuple into a FreeCAD matrix"""

    # https://github.com/IfcOpenShell/IfcOpenShell/issues/1440
    # https://pythoncvc.net/?cat=203
    m_l = list()
    for i in range(3):
        line = list(ios_matrix[i::3])
        line[-1] *= SCALE
        m_l.extend(line)
    return FreeCAD.Matrix(*m_l)


def save_ifc(obj):

    """Saves the linked IFC file of an object"""

    if hasattr(obj,"FilePath") and obj.FilePath:
        ifcfile = get_ifcfile(obj)
        ifcfile.write(obj.FilePath)
        obj.Modified = False
        FreeCAD.Console.PrintMessage("Saved " + obj.FilePath + "\n")


def get_elem_attribs(ifcentity):

    # usually info_ifcentity = ifcentity.get_info() would de the trick
    # the above could raise an unhandled excption on corrupted ifc files in IfcOpenShell
    # see https://github.com/IfcOpenShell/IfcOpenShell/issues/2811
    # thus workaround

    info_ifcentity = {
        "id": ifcentity.id(),
        "type": ifcentity.is_a()
    }

    # get attrib keys
    attribs = []
    for anumber in range(20):
        try:
            attr = ifcentity.attribute_name(anumber)
        except Exception:
            break
        # print(attr)
        attribs.append(attr)

    # get attrib values
    for attr in attribs:
        try:
            value = getattr(ifcentity, attr)
        except Exception as e:
            # print(e)
            value = "Error: {}".format(e)
            print(
                "DEBUG: The entity #{} has a problem on attribut {}: {}"
                .format(ifcentity.id(), attr, e)
            )
        # print(value)
        info_ifcentity[attr] = value

    return info_ifcentity
