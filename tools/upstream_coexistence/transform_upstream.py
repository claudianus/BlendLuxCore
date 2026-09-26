#!/usr/bin/env python3
"""Rename upstream BlendLuxCore identifiers/strings so it can coexist
with the SuperLuxCore fork as a separate Blender extension.

Identifier rules:
  classmap-driven: every class registered with bpy gets a unique name.
    - LuxCore*/LUXCORE*/luxcore* prefixed classes -> Up/UP/_up prefixed
    - other bpy-registered classes (LOL ops, OBJECT_MT_*, prefs) -> name+"_UP"
    - helper classes (Color, Stat, ...) are left alone so imports of same-named
      bpy/mathutils names don't break.
  pyluxcore -> pyluxcore_upstream
  .luxcore / ops.luxcore / luxcoreOL / luxcore_* identifiers -> _up variants
Strings get matching rewrites so bl_idname/bl_parent_id/op calls stay consistent.
"""

import io
import pathlib
import re
import sys
import tokenize

SRC = pathlib.Path(sys.argv[1])
DST = pathlib.Path(sys.argv[2])

BPY_BASES = {
    "Panel", "Operator", "Menu", "PropertyGroup", "RenderEngine", "UIList",
    "AddonPreferences", "NodeTree", "Node", "NodeSocket", "Header", "Gizmo",
    "GizmoGroup", "Macro", "OperatorFileListElement", "FileBrowser",
    "OperatorProperties", "KeyingSet", "AssetShelf", "ImageButtons",
    "CompositorNode", "TextureNode", "GeometryNode", "ShaderNode",
    "FunctionNode", "NodeCustomInterface", "NodeSocketInterface",
    "UI_UL_list", "NodesSocket", "WidgetGroup", "NodeTreeInterfaceSocket",
}

PREFIXED = ("LuxCore", "LUXCORE", "luxcore")


def prefixed_new(name):
    if name.startswith("LuxCore"):
        return "LuxCoreUp" + name[len("LuxCore"):]
    if name.startswith("LUXCORE"):
        return "LUXCOREUP" + name[len("LUXCORE"):]
    if name.startswith("luxcore"):
        return "luxcore_up" + name[len("luxcore"):]
    return None


# ---------- pass 1: collect class names + bases + registered-class refs ----------
class_bases = {}          # name -> [base name tokens]
registered_refs = set()   # names seen in `classes = (...)` / register_class(X)

for py in SRC.rglob("*.py"):
    try:
        text = py.read_text(encoding="utf-8")
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, UnicodeDecodeError, SyntaxError):
        continue

    i = 0
    while i < len(toks):
        tok = toks[i]
        # `class Name(bases):`  -> record bases text
        if tok.type == tokenize.NAME and tok.string == "class" and i + 1 < len(toks):
            nxt = toks[i + 1]
            if nxt.type == tokenize.NAME:
                bases = []
                j = i + 2
                if j < len(toks) and toks[j].string == "(":
                    depth = 0
                    while j < len(toks):
                        if toks[j].string == "(":
                            depth += 1
                        elif toks[j].string == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        elif toks[j].type == tokenize.NAME:
                            bases.append(toks[j].string)
                        j += 1
                class_bases[nxt.string] = bases
                i = j
        i += 1

    # `classes = (...)` tuples and register_class(X)
    for m in re.finditer(r"\bclasses\s*=\s*[\(\[]([^\)\]]*)", text):
        registered_refs.update(re.findall(r"\.?([A-Za-z_]\w*)\s*,?", m.group(1)))
    for m in re.finditer(r"register_class\(\s*([A-Za-z_]\w*)", text):
        registered_refs.add(m.group(1))

# classes to rename: prefixed, or bpy-based/registered (with closure over
# codebase-defined bases so subclasses of renamed classes follow)
classmap = {}
for name in class_bases:
    new = prefixed_new(name)
    if new:
        classmap[name] = new

def is_bpyish(bases):
    for b in bases:
        if b in BPY_BASES or b in classmap:
            return True
    return False

changed = True
while changed:
    changed = False
    for name, bases in class_bases.items():
        if name in classmap:
            continue
        if name in registered_refs or is_bpyish(bases):
            classmap[name] = name + "_UP"
            changed = True

# ---------- identifier rules ----------
def map_name(tok):
    if tok in classmap:
        return classmap[tok]
    if tok == "pyluxcore":
        return "pyluxcore_upstream"
    if tok == "luxcoreOL":
        return "luxcoreOL_up"
    if tok.startswith("luxcore"):
        return "luxcore_up" + tok[len("luxcore"):]
    if tok.startswith("LuxCore"):
        return "LuxCoreUp" + tok[len("LuxCore"):]
    if tok.startswith("LUXCORE"):
        return "LUXCOREUP" + tok[len("LUXCORE"):]
    return tok

# ---------- string rules ----------
WORD = r"A-Za-z0-9_"
def map_string(s):
    if s in classmap:
        return classmap[s]
    s2 = s.replace("luxcorerender", "\x00").replace("LuxCoreRender", "\x01")
    s2 = re.sub(rf"(?<![{WORD}])pyluxcore(?![{WORD}])", "pyluxcore_upstream", s2)
    s2 = re.sub(rf"(?<![{WORD}])luxcoreOL", "luxcoreOL_up", s2)
    s2 = re.sub(rf"(?<![{WORD}])luxcore", "luxcore_up", s2)
    s2 = re.sub(rf"(?<![{WORD}])LUXCORE", "LUXCOREUP", s2)
    s2 = re.sub(rf"(?<![{WORD}])LuxCore(?=[{WORD}])", "LuxCoreUp", s2)
    s2 = re.sub(rf"(?<![{WORD}])LuxCore(?![{WORD}])", "LuxCore Upstream", s2)
    s2 = re.sub(rf"(?<![{WORD}])blendluxcore(?![{WORD}])", "blendluxcore_up", s2)
    return s2.replace("\x00", "luxcorerender").replace("\x01", "LuxCoreRender")

def split_literal(tokval):
    m = re.match(r"(?i)([rubf]*)('''|\"\"\"|'|\")", tokval)
    if not m:
        return None
    prefix, quote = m.group(1), m.group(2)
    body = tokval[len(prefix) + len(quote):]
    if body.endswith(quote):
        body = body[: -len(quote)]
    return prefix, quote, body

def transform_source(src):
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        return src
    for tok in toks:
        ttype, tstring = tok.type, tok.string
        if ttype == tokenize.NAME:
            tstring = map_name(tstring)
        elif ttype == tokenize.STRING:
            parts = split_literal(tstring)
            if parts and "b" not in parts[0].lower():
                prefix, quote, body = parts
                tstring = prefix + quote + map_string(body) + quote
            else:
                tstring = map_string(tstring)
        elif hasattr(tokenize, "FSTRING_MIDDLE") and ttype == tokenize.FSTRING_MIDDLE:
            tstring = map_string(tstring)
        out.append((ttype, tstring, tok.start, tok.end, tok.line))
    return tokenize.untokenize(out)

# ---------- run ----------
DST.mkdir(parents=True, exist_ok=True)
changed_n, copied = 0, 0
for path in SRC.rglob("*"):
    rel = path.relative_to(SRC)
    if ".git" in rel.parts or "__pycache__" in rel.parts:
        continue
    target = DST / rel
    if path.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".py":
        target.write_text(transform_source(path.read_text(encoding="utf-8")),
                          encoding="utf-8")
        changed_n += 1
    else:
        target.write_bytes(path.read_bytes())
        copied += 1

print(f"classes renamed: {len(classmap)}")
for k in sorted(classmap):
    if not k.startswith(PREFIXED):
        print("  non-prefixed:", k, "->", classmap[k])
print(f"py transformed: {changed_n}, other copied: {copied}")
