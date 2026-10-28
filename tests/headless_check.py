# 无人值守冒烟测试：headless 环境下验证插件注册与核心功能
# 用法: blender --background --python tests/headless_check.py
import importlib

import bpy

MOD = 'quickanim'
results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print('CHECK_%s: %s' % (name, 'PASS' if cond else 'FAIL'))


# 1. 启用插件
try:
    bpy.ops.preferences.addon_enable(module=MOD)
    check('enable', True)
except Exception as e:
    check('enable: %r' % e, False)

check('enabled', MOD in bpy.context.preferences.addons.keys())
check('scene_prop', hasattr(bpy.types.Scene, 'quickanim'))

# 2. 面板与操作符已注册
check('panel_registered', hasattr(bpy.types, 'QUICKANIM_PT_main'))
for opid in ['quickanim.key_camera', 'quickanim.fit_frame', 'quickanim.mmd_mode',
             'quickanim.record_camera', 'quickanim.preset', 'quickanim.make_loop',
             'quickanim.render_animation', 'quickanim.toggle_autokey',
             'quickanim.delete_keys', 'quickanim.clear_animation']:
    check('op_' + opid.split('.')[1], hasattr(bpy.ops.quickanim, opid.split('.')[1]))

# 3. 核心函数级测试：创建摄像机并 K 帧
mod = importlib.import_module(MOD)
scene = bpy.context.scene
cam_data = bpy.data.cameras.new('TestCam')
cam_obj = bpy.data.objects.new('TestCam', cam_data)
scene.collection.objects.link(cam_obj)
scene.camera = cam_obj

try:
    mod._key_camera(cam_obj, 1)
    cam_obj.location = (1, 2, 3)
    mod._key_camera(cam_obj, 10)
    ad = cam_obj.animation_data
    check('camera_fcurves', ad is not None and ad.action is not None and len(ad.action.fcurves) >= 3)
except Exception as e:
    check('camera_fcurves: %r' % e, False)

# 4. 物体 K 帧
try:
    bpy.ops.mesh.primitive_cube_add(size=2)
    cube = bpy.context.active_object
    mod._key_object(cube, 1)
    cube.location = (5, 0, 0)
    mod._key_object(cube, 20)
    check('object_fcurves', cube.animation_data is not None and cube.animation_data.action is not None)
except Exception as e:
    check('object_fcurves: %r' % e, False)

# 5. 渲染预设与输出格式操作符（仅场景设置，不需 GUI）
try:
    bpy.ops.quickanim.preset(preset='PREVIEW')
    check('preset_eevee', scene.render.engine == 'BLENDER_EEVEE')
    bpy.ops.quickanim.preset(preset='FINAL')
    check('preset_cycles', scene.render.engine == 'CYCLES')
except Exception as e:
    check('preset: %r' % e, False)

try:
    bpy.ops.quickanim.set_output_png()
    check('output_png', scene.render.image_settings.file_format == 'PNG')
    bpy.ops.quickanim.set_output_mp4()
    check('output_mp4', scene.render.image_settings.file_format == 'FFMPEG')
except Exception as e:
    check('output: %r' % e, False)

# 6. 贴合画框相关设置
try:
    settings = scene.quickanim
    settings.fit_on_mmd = True
    check('fit_settings', getattr(settings, 'fit_on_mmd', False) is True)
except Exception as e:
    check('fit_settings: %r' % e, False)

# 7. 图标有效性检查：面板用到的所有图标必须在当前 Blender 中存在
try:
    params = bpy.types.UILayout.bl_rna.functions['operator'].parameters['icon']
    valid_icons = {i.identifier for i in params.enum_items}
    check('icons_enum_available', True)
except Exception:
    valid_icons = None
    check('icons_enum_available', False)

USED_ICONS = [
    'KEYINGSET', 'CHECKBOX_HLT', 'CHECKBOX_DEHLT', 'DECORATE_KEYFRAME',
    'ARMATURE_DATA', 'COPYDOWN', 'PASTEDOWN', 'MOD_MIRROR', 'FILE_REFRESH',
    'VIEW_CAMERA', 'CAMERA_DATA', 'FRAME_NEXT', 'CANCEL', 'REC',
    'RESTRICT_RENDER_OFF', 'FILE_IMAGE', 'FILE_MOVIE', 'PROP_CON',
    'FILE_FOLDER', 'RENDER_STILL', 'RENDER_ANIMATION', 'X', 'TRASH',
]
if valid_icons is not None:
    missing = [i for i in USED_ICONS if i not in valid_icons]
    check('icons_all_valid', not missing)
    if missing:
        print('MISSING_ICONS:', missing)

# 8. 删除关键帧功能
try:
    scene.frame_set(10)
    cam_obj.select_set(True)
    bpy.context.view_layer.objects.active = cam_obj
    fcu0 = cam_obj.animation_data.action.fcurves[0]
    before = len(fcu0.keyframe_points)
    bpy.ops.quickanim.delete_keys()
    after = len(fcu0.keyframe_points)
    check('delete_keys_at_frame', after < before)
except Exception as e:
    check('delete_keys_at_frame: %r' % e, False)

try:
    bpy.ops.quickanim.clear_animation('EXEC_DEFAULT')
    ad = cam_obj.animation_data
    check('clear_animation', ad is None or ad.action is None)
except Exception as e:
    check('clear_animation: %r' % e, False)

# 汇总
fails = [n for n, ok in results if not ok]
print('SUMMARY: %d/%d passed' % (len(results) - len(fails), len(results)))
if fails:
    print('FAILED:', fails)
