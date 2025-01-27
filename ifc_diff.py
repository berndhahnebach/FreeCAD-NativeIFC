#***************************************************************************
#*                                                                         *
#*   Copyright (c) 2023 Yorik van Havre <yorik@uncreated.net>              *
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

"""Diffing tool for NativeIFC project objects"""

import os
import difflib
import FreeCADGui
import ifcopenshell


def get_diff(proj):
    
    """Obtains a diff between the current version and the saved version of a project"""
    
    if not proj.FilePath:
        return
    # cannot use open() here as it gives different encoding 
    # than ifcopenshell and diff does not work
    f = ifcopenshell.open(proj.FilePath)
    old = f.wrapped_data.to_string().split("\n")
    new = proj.Proxy.ifcfile.wrapped_data.to_string().split("\n")
    #diff = difflib.HtmlDiff().make_file(old,new) # UGLY
    res = [l for l in difflib.unified_diff(old, new, lineterm = "")]
    res = [l for l in res if l.startswith("+") or l.startswith("-")]
    res = [l for l in res if not l.startswith("+++") and not l.startswith("---")]
    return "\n".join(res)


def htmlize(diff):
    
    """Returns an HTML version of a diff list"""

    diff = diff.split("\n")
    html = "<html><body>\n"
    for l in diff:
        if l.startswith("+"):
            html += "<span style='color:green;'>" + l[:100] + "</span><br/>\n"
        elif l.startswith("-"):
            html += "<span style='color:red;'>" + l[:100] + "</span><br/>\n"
        else:
            html += l + "<br/>\n"
    html += "</body></html>"
    return html


def show_diff(diff):
    
    """Shows a dialog showing the diff contents"""
    
    b = os.path.dirname(__file__)
    dlg = FreeCADGui.PySideUic.loadUi(os.path.join(b, "ui", "dialogDiff.ui"))
    dlg.textEdit.setHtml(htmlize(diff))
    result = dlg.exec_()
    
