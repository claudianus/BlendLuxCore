# Cycles shader-node coverage (Blender 5.2 LTS)

Audit of `export/cycles_node_reader.py` against the 102 `ShaderNode*`
types registered by Blender 5.2.1.

Unsupported nodes never silently black out: the reader logs a warning
(node name + reason) and emits a neutral fallback — grey `matte` for
shader outputs, mid-grey/identity for value/vector sockets, or passes
through the first input where that is semantically closest. Node types
with no SuperLuxCore equivalent additionally carry a specific reason via
`_UNSUPPORTED_NODE_NOTES`.

## Legend

- **mapped** — the primary path converts; uncommon sockets/modes may warn + fall back
- **approx** — mapped with a documented approximation (warning emitted)
- **const-only** — works when key inputs are constant; textured inputs warn + passthrough
- **warn** — no SuperLuxCore equivalent; warning + neutral fallback
- **n/a** — not reachable through material trees (handled elsewhere or engine-internal)

## BSDF / shader nodes

| Node | Status | Notes |
|---|---|---|
| ShaderNodeBsdfPrincipled | approx | Disney mapping: base/metallic/roughness/IOR/alpha/normal, specular level+tint (tint = luminance of free color, warns when colored), subsurface weight (diffuse-profile approx — radius/scale/IOR/anisotropy and random-walk methods warn), anisotropic (rotation/tangent warn), sheen weight+tint (sheen roughness warns), coat weight/roughness → clearcoat/gloss; non-default coat IOR/tint/normal → real `glossycoating` layer (ks=weight, index=IOR, ka/d=tint absorption matching `pow(tint, w/cosNT)`, bumptex=coat normal); coat also wraps the glass path; transmission weight/roughness → integrated lobe or glass/roughglass; Thin Wall + sharp full transmission → archglass (else warn); thin film → film params (disney + glass families); emission = color×strength; diffuse roughness warns |
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
| ShaderNodeCameraData | warn | view vector/depth unavailable to SuperLuxCore textures |
| ShaderNodeLightPath | mapped | all outputs via `rayinfo` texture (HitPoint ray context): Is Camera/Shadow/Diffuse/Glossy/Singular/Reflection/Transmission/Volume Scatter Ray, Ray Length/Depth, Diffuse/Glossy/Transparent/Transmission Depth |
| ShaderNodeLayerWeight | approx | Facing→0.5; Fresnel→Schlick F0 |
| ShaderNodeFresnel | approx | Schlick F0 (no angular Fresnel texture) |
| ShaderNodeVolumeInfo | approx | per-output subset warns |
| ShaderNodeRaycast | warn | no scene-query textures |
| ShaderNodeTangent | approx | |
| ShaderNodeUVAlongStroke | warn | Freestyle |

## Color / math / vector nodes

| Node | Status | Notes |
|---|---|---|
| ShaderNodeMath | mapped | SINE/COSINE/TANGENT/ARC*/ARCTAN2/SINH/COSH/TANH/INVERSE_SQRT/FLOORED_MODULO/EXPONENT/LOGARITHM via SuperLuxCore `mathfunc` texture (log_b(x)=ln(x)/ln(b)); SMOOTH_MIN/SMOOTH_MAX via polynomial composition; SQRT/MIN/MAX/FLOOR/CEIL/TRUNC/FRACT/PINGPONG/SIGN/COMPARE/WRAP/SNAP/MULTIPLY_ADD/RADIANS/DEGREES composed from existing textures; remaining ops warn + passthrough |
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
| ShaderNodeBevel | mapped | SuperLuxCore bevel texture (bump-only round edges; Radius constant only, Normal input unsupported) |
| ShaderNodeAmbientOcclusion | approx | AO texture |
| ShaderNodeWireframe | mapped | wireframe |
| ShaderNodeDisplacement / VectorDisplacement | approx | object space only; exported as displacement shape |
| ShaderNodeGroup / CustomGroup | mapped | recursive expansion |
| ShaderNodeOutputMaterial | n/a | entry point, not evaluated as a node |
| ShaderNodeOutputWorld / OutputLight | n/a | world/light trees (see below) |
| ShaderNodeOutputAOV | warn | SuperLuxCore AOVs via film outputs, not material nodes |
| ShaderNodeScript | warn | no OSL |

## World & light trees

Handled outside `_node` (`export/light.py::_convert_cycles_world` /
`_convert_cycles_light`): Background, TexSky, TexEnvironment, Emission,
portal lights. `ShaderNodeLightFalloff` warns (falloff lives on SuperLuxCore
light definitions).

## Regression test

`dev-tools/cycles_node_coverage_test.py` (headless Blender, non-render):
constant-fold checks for the composed vector ops, material-type checks
for the new BSDF mappings, warning-content checks for the warn-tier
nodes, and Principled coverage: disney/glossycoating coat wrap
(`base`/`ks`/`index`/`ka`/`d`/coat `bumptex`), archglass thin-wall path,
thin-film on glass, and per-feature warning assertions.

## E2E render corpus

`dev-tools/e23_cycles_compat_e2e_test.py` (headless Blender, renders):

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background \
    --factory-startup --python dev-tools/e23_cycles_compat_e2e_test.py
```

Builds 14 small scenes (128×128, ~32 samples) that use only native
Cycles node trees, renders each with SuperLuxCore (PATH, CPU) and asserts the
output is finite, non-black and plausibly bright. Where the result is
physically comparable the same scene is also rendered with Cycles (CPU)
and the mean luminance factor plus mean-normalised luminance RMSE are
compared with loose tolerances. Renders are written as EXR and read back
via `bpy.data.images.load()`, so all checks run on scene-linear radiance
(the Render Result buffer is not readable in background mode).

Scene coverage:

| Scene | Nodes exercised | Parity vs Cycles |
|---|---|---|
| s01_diffuse_point | BsdfDiffuse + point light | asserted |
| s02_principled_area | BsdfPrincipled + area light | asserted (disney approx) |
| s03_glass_sun | BsdfGlass + sun light | asserted, loose (caustics) |
| s04_emission | Emission shader | asserted |
| s05_noise_ramp | TexNoise -> ValToRGB | asserted, loose (approx noise) |
| s06_image_texture | TexImage (packed generated) | asserted + quadrant hues |
| s07_bump_noise | TexNoise -> Bump | asserted, loose |
| s08_mix_transparent | TexChecker -> MixShader(Transparent, Diffuse) | asserted |
| s09_world_background | world Background flat color | asserted |
| s10_world_sky | world TexSky(Hosek-Wilkie) -> sky2 | asserted, loose |
| s11_volume_principled | VolumePrincipled interior volume | asserted, loose |
| s12_lightpath | LightPath Is Camera Ray -> MixShader | asserted |
| s13_shadertorgb_fallback | ShaderToRGB warn-tier fallback | SuperLuxCore-only + warning check |
| s14_lightpath_mirror | LightPath Is Camera Ray across a mirror bounce | asserted + quadrant hues |

Notes:

- Lights and worlds must opt in via `superluxcore.use_cycles_settings`;
  materials with a Blender node tree are converted automatically.
- The test disables `path.auto_clamping` / `use_clamping` and resets
  `suggested_clamping_value` for deterministic parity measurements.
  `check_stale_clamp()` additionally verifies the stale-suggestion fix:
  the suggested clamp is stamped with a lighting-content signature
  (`suggested_clamping_sig`), so a value measured on one scene is ignored
  once the emitters change — before the fix, a previous scene's clamp
  silently crushed bright emitters (~13x observed on s04).
- Measured luminance factors on this suite are ~1.0–1.6 for mapped nodes
  and ~2.0 for the approx-tier noise texture; the warn-tier scenes render
  non-black via the documented fallbacks.
- `ShaderNodeLightPath` is backed by the `rayinfo` texture:
  `Scene::Intersect()` records the incoming ray's flags (camera/shadow/
  indirect), the generating BSDF event and the path depth counters on the
  shaded `HitPoint` (`rayFlags`, `rayEvent`, `rayDepth`, `rayDiffuseDepth`,
  `rayGlossyDepth`, `raySpecularDepth`, `rayTransmissionDepth`,
  `rayTransparentDepth`, `rayLength`). Camera and shadow rays mask the
  event-based outputs to 0 (they have no generating bounce), matching
  Cycles where `is_*_ray` flags other than camera/shadow are 0 on the
  first path vertex. Works on PATHCPU and PATHOCL; BiDir/light-tracing
  hits carry the corresponding light-path context.

