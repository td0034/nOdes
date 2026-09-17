#!/usr/bin/env python3
"""Assemble turing_designer.html from its two sources.

turing_core.js       geometry + SDF + mesher (also runs in Node for tests)
turing_app_shell.html  UI + three.js viewport, with a /*__CORE__*/ marker

Edit the sources, run this, open turing_designer.html.
"""
import pathlib
here = pathlib.Path(__file__).parent
core = (here / "turing_core.js").read_text().split(
    'if(typeof module!=="undefined")')[0]
for shell_name, out_name in [("turing_app_shell.html", "turing_designer.html"),
                             ("turing_evolver_shell.html", "turing_evolver.html")]:
    shell = (here / shell_name).read_text()
    assert "/*__CORE__*/" in shell
    (here / out_name).write_text(shell.replace("/*__CORE__*/", core))
    print("built", out_name)
