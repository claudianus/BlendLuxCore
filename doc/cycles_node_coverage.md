# Cycles shader-node coverage (Blender 5.2 LTS)

Audit of `export/cycles_node_reader.py` against the 102 `ShaderNode*`
types registered by Blender 5.2.1.

Unsupported nodes never silently black out: the reader logs a warning
(node name + reason) and emits a neutral fallback — grey `matte` for
shader outputs, mid-grey/identity for value/vector sockets, or passes
through the first input where that is semantically closest. Node types
with no LuxCore equivalent additionally carry a specific reason via
`_UNSUPPORTED_NODE_NOTES`.

## Legend

- **mapped** — the primary path converts; uncommon sockets/modes may warn + fall back
- **approx** — mapped with a documented approximation (warning emitted)
- **const-only** — works when key inputs are constant; textured inputs warn + passthrough
- **warn** — no LuxCore equivalent; warning + neutral fallback
- **n/a** — not reachable through material trees (handled elsewhere or engine-internal)

## BSDF / shader nodes

| Node | Status | Notes |
|---|---|---|
| ShaderNodeBsdfPrincipled | approx | Disney mapping; subsurface radius, coat IOR/tint/normal, sheen roughness, anisotropic rotation, thin wall unsupported (warn) |
| ShaderNodeBsdfDiffuse | mapped | matte |
| ShaderNodeBsdfGlossy | mapped | glossy2 |
| ShaderNodeBsdfAnisotropic | mapped | glossy2 anisotropic |
| ShaderNodeBsdfMetallic | mapped | metal2; F82 tint warns |
| ShaderNodeBsdfGlass | mapped | glass/roughglass; thin-film warns |
| ShaderNodeBsdfRefraction | mapped | |
| ShaderNodeBsdfTransparent | mapped | transparent |
| ShaderNodeBsdfTranslucent | mapped | mattetranslucent |
| ShaderNodeBsdfVelvet | approx | |
| ShaderNodeBsdfSheen | approx | |
| ShaderNodeBsdfToon | approx | |
| ShaderNodeBsdfHairPrincipled | approx | Marschner `hairmat`; melanin/color/absorption parametrizations; textured melanin → const approx |
| ShaderNodeBsdfHair | approx | legacy hair → hairmat; Reflection/Transmission lobe split not separable |
| ShaderNodeBsdfRayPortal | approx | → transparent (rays pass through) |
| ShaderNodeEeveeSpecular | approx | legacy Eevee specular → glossy2 |
| ShaderNodeEmission | mapped | |
| ShaderNodeBackground | warn | world shader only; warns inside material trees |
| ShaderNodeHoldout | approx | |
| ShaderNodeSubsurfaceScattering | approx | |
| ShaderNodeAddShader | mapped | |
| ShaderNodeMixShader | mapped | |
| ShaderNodeShaderToRGB | warn | Eevee-only concept |

## Texture nodes

| Node | Status | Notes |
|---|---|---|
| ShaderNodeTexImage | mapped | incl. UV/Generated/Object vector mapping |
| ShaderNodeTexEnvironment | mapped | |
| ShaderNodeTexNoise | approx | blender_noise; feature subset warns |
| ShaderNodeTexVoronoi | approx | distance/feature subset warns |
| ShaderNodeTexBrick | mapped | brick |
| ShaderNodeTexChecker | mapped | checkerboard |
| ShaderNodeTexGradient | approx | gradient-type subset |
| ShaderNodeTexMagic | approx | blender_magic |
| ShaderNodeTexWave | approx | wave-type subset |
| ShaderNodeTexWhiteNoise | mapped | whitenoise |
| ShaderNodeTexGabor | mapped | `gabornoise` (native; 2D only, 3D mode warns+approximates) |
| ShaderNodeTexIES | mapped | light Emission strength -> mappoint/mapsphere iesblob (point lights) |
| ShaderNodeTexSky | warn | sky2/sun are lights, not material textures |

## Input / geometry nodes

| Node | Status | Notes |
|---|---|---|
| ShaderNodeTexCoord | approx | UV/Normal/Object mapped; Generated→UV, Reflection→normal approximations warn; Window/Camera warn |
| ShaderNodeNewGeometry | approx | per-output support varies; unsupported outputs warn |
| ShaderNodeUVMap | mapped | incl. named-layer index lookup |
| ShaderNodeAttribute | mapped | color attrs, UV layers (Vector out), generic named attrs — float/int/bool→`hitpointvertexaov`/`hitpointtriangleaov`, vector/float2→extra color layer; edge-domain/string warn |
| ShaderNodeVertexColor | approx | |
| ShaderNodeObjectInfo | approx | Random→objectidnormalized; per-field subset warns |
| ShaderNodeParticleInfo | approx | per-field subset warns |
| ShaderNodeHairInfo | approx | per-output subset warns |
| ShaderNodePointInfo | approx | Random→objectidnormalized (per-point instance id); Position→hit position approx; Radius→warn 1.0 |
| ShaderNodeCameraData | warn | view vector/depth unavailable to LuxCore textures |
| ShaderNodeLightPath | warn | no ray-type info; Is Camera Ray→1 else 0 |
| ShaderNodeLayerWeight | approx | Facing→0.5; Fresnel→Schlick F0 |
| ShaderNodeFresnel | approx | Schlick F0 (no angular Fresnel texture) |
| ShaderNodeVolumeInfo | approx | per-output subset warns |
| ShaderNodeRaycast | warn | no scene-query textures |
| ShaderNodeTangent | approx | |
| ShaderNodeUVAlongStroke | warn | Freestyle |

## Color / math / vector nodes

| Node | Status | Notes |
|---|---|---|
| ShaderNodeMath | mapped | SINE/COSINE/TANGENT/ARC*/ARCTAN2/SINH/COSH/TANH/INVERSE_SQRT/FLOORED_MODULO/EXPONENT/LOGARITHM via LuxCore `mathfunc` texture (log_b(x)=ln(x)/ln(b)); SMOOTH_MIN/SMOOTH_MAX via polynomial composition; SQRT/MIN/MAX/FLOOR/CEIL/TRUNC/FRACT/PINGPONG/SIGN/COMPARE/WRAP/SNAP/MULTIPLY_ADD/RADIANS/DEGREES composed from existing textures; remaining ops warn + passthrough |
| ShaderNodeVectorMath | approx | ADD/SUBTRACT/MULTIPLY/DIVIDE/DOT/CROSS/REFLECT/PROJECT/FACEFORWARD/MULTIPLY_ADD/LENGTH/DISTANCE/NORMALIZE/SCALE/ABSOLUTE/MODULO/MIN/MAX mapped; SNAP→nearest-multiple approx; SINE/COSINE/TANGENT→elementwise `mathfunc`; WRAP/FLOORMOD/REFRACT→warn passthrough |
| ShaderNodeVectorRotate | const-only | rotation composed as constant 3x3 matrix over texture channels; textured axis/angle/euler → warn passthrough |
| ShaderNodeVectorTransform | approx | world/object/camera matrices composed per object; per-instance object space not expressible (base object matrix used); unresolvable → warn passthrough |
| ShaderNodeMixRGB / Mix | approx | direct blend modes; exotic blends → mix + warn |
| ShaderNodeValToRGB (ColorRamp) | mapped | band |
| ShaderNodeRGBCurve / FloatCurve / VectorCurve | mapped | curve LUT evaluation |
| ShaderNodeHueSaturation | mapped | hsv |
| ShaderNodeInvert | mapped | |
| ShaderNodeBrightContrast | mapped | brightcontrast |
| ShaderNodeGamma | mapped | power |
| ShaderNodeRGBToBW | mapped | Rec.709 dot product |
| ShaderNodeMapRange | mapped | remap |
| ShaderNodeClamp | mapped | legacy node |
| ShaderNodeCombine*/Separate* | mapped | makefloat3/splitfloat3; non-RGB modes warn |
| ShaderNodeRGB / Value | mapped | constfloat3/constfloat1 |
| ShaderNodeBlackbody | mapped | blackbody |
| ShaderNodeWavelength | mapped | lampspectrum |
| ShaderNodeSqueeze | mapped | sigmoid 1/(1+exp(-(v-c)*w)) via `mathfunc` exp + arithmetic |

## Shading-graph utility nodes

| Node | Status | Notes |
|---|---|---|
| ShaderNodeMapping | mapped | UV/global mapping transforms |
| ShaderNodeNormal | approx | direction output subset warns |
| ShaderNodeNormalMap | mapped | tangent-space normal maps |
| ShaderNodeBump | mapped | bump mapping |
| ShaderNodeBevel | approx | LuxCore bevel texture (round edges) |
| ShaderNodeAmbientOcclusion | approx | AO texture |
| ShaderNodeWireframe | mapped | wireframe |
| ShaderNodeDisplacement / VectorDisplacement | approx | object space only; exported as displacement shape |
| ShaderNodeGroup / CustomGroup | mapped | recursive expansion |
| ShaderNodeOutputMaterial | n/a | entry point, not evaluated as a node |
| ShaderNodeOutputWorld / OutputLight | n/a | world/light trees (see below) |
| ShaderNodeOutputAOV | warn | LuxCore AOVs via film outputs, not material nodes |
| ShaderNodeScript | warn | no OSL |

## World & light trees

Handled outside `_node` (`export/light.py::_convert_cycles_world` /
`_convert_cycles_light`): Background, TexSky, TexEnvironment, Emission,
portal lights. `ShaderNodeLightFalloff` warns (falloff lives on LuxCore
light definitions).

## Regression test

`dev-tools/cycles_node_coverage_test.py` (headless Blender, non-render):
constant-fold checks for the composed vector ops, material-type checks
for the new BSDF mappings, and warning-content checks for the warn-tier
nodes.
