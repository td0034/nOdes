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
shell = (here / "turing_app_shell.html").read_text()
assert "/*__CORE__*/" in shell
(here / "turing_designer.html").write_text(shell.replace("/*__CORE__*/", core))
print("built turing_designer.html")
