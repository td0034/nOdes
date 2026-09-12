#!/usr/bin/env python3
"""Assemble silicone_designer.html from its two sources.

silicone_core.js       geometry + SDF + mesher (also runs in Node for tests)
silicone_app_shell.html  UI + three.js viewport, with a /*__CORE__*/ marker

Edit the sources, run this, open silicone_designer.html.
"""
import pathlib
here = pathlib.Path(__file__).parent
core = (here / "silicone_core.js").read_text().split(
    'if(typeof module!=="undefined")')[0]
shell = (here / "silicone_app_shell.html").read_text()
assert "/*__CORE__*/" in shell
(here / "silicone_designer.html").write_text(shell.replace("/*__CORE__*/", core))
print("built silicone_designer.html")
