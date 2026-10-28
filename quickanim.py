# -*- coding: utf-8 -*-
"""
QuickAnim — 快速动画与渲染助手
================================
把「常用 K 帧操作」「MMD 风格摄像机 K 帧」和「一键渲染」整合进 Blender 侧边栏。

安装：编辑 > 偏好设置 > 插件 > 安装 > 选择本文件 > 勾选启用。
使用：3D 视图右侧边栏（按 N 键）> QuickAnim 标签页。

作者：DSH
版本：1.2.0
"""

import os
import sys

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    StringProperty,
    EnumProperty,
    BoolProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
)
from bpy.types import Panel, Operator, AddonPreferences, PropertyGroup


bl_info = {
    "name": "QuickAnim 快速动画与渲染助手",
    "author": "DSH",
    "version": (1, 2, 0),
    "blender": (3, 0, 0),
    "location": "3D 视图 > 侧边栏(N) > QuickAnim",
    "description": "一键 K 帧、MMD 风格摄像机、姿势复用、循环动画、快速渲染",
    "category": "Animation",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _key_object(obj, frame):
    """为普通物体插入 位置/旋转/缩放 关键帧。"""
    try:
        if obj.rotation_mode == 'QUATERNION':
            obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        else:
            obj.keyframe_insert(data_path="rotation_euler", frame=frame)
    except Exception:
        pass
    try:
        obj.keyframe_insert(data_path="location", frame=frame)
    except Exception:
        pass
    try:
        obj.keyframe_insert(data_path="scale", frame=frame)
    except Exception:
        pass


def _key_pose_bone(pb, frame):
    """为单个姿态骨骼插入 位置/旋转/缩放 关键帧。"""
    try:
        pb.keyframe_insert(data_path="location", frame=frame)
    except Exception:
        pass
    try:
        if pb.rotation_mode == 'QUATERNION':
            pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        else:
            pb.keyframe_insert(data_path="rotation_euler", frame=frame)
    except Exception:
        pass
    try:
        pb.keyframe_insert(data_path="scale", frame=frame)
    except Exception:
        pass


def _key_camera(cam, frame):
    """为摄像机插入 位置/旋转/焦距 关键帧。"""
    try:
        cam.keyframe_insert(data_path="location", frame=frame)
    except Exception:
        pass
    try:
        if cam.rotation_mode == 'QUATERNION':
            cam.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        else:
            cam.keyframe_insert(data_path="rotation_euler", frame=frame)
    except Exception:
        pass
    if cam.data is not None:
        try:
            cam.data.keyframe_insert(data_path="lens", frame=frame)
        except Exception:
            pass


def _selected_pose_bones(obj):
    """返回选中的姿态骨骼；若没有选中则返回全部（方便一键整骨架）。"""
    sel = [pb for pb in obj.pose.bones if pb.bone.select]
    return sel if sel else list(obj.pose.bones)


def _selected_fcurves(context):
    """返回当前选中物体的动画曲线列表（覆盖物体与骨骼通道）。"""
    fcus = []
    for o in context.selected_objects:
        ad = getattr(o, "animation_data", None)
        if ad and ad.action:
            fcus.extend(ad.action.fcurves)
    return fcus


def _armature_in_pose_mode(context):
    obj = context.object
    return obj is not None and obj.type == 'ARMATURE' and obj.mode == 'POSE'


def _ensure_camera(context):
    """确保场景有激活摄像机，没有就新建一个。"""
    scene = context.scene
    if scene.camera is not None:
        return scene.camera
    cam = context.object if (context.object and context.object.type == 'CAMERA') else None
    if cam is None:
        bpy.ops.object.camera_add()
        cam = context.object
    scene.camera = cam
    return cam


def _set_view_to_camera(context, lock=True):
    """把所有 3D 视图切到摄像机视角，并（可选）锁定摄像机到视图。"""
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type != 'VIEW_3D':
                continue
            try:
                space.region_3d.view_perspective = 'CAMERA'
            except Exception:
                pass
            try:
                space.lock_camera = lock
            except Exception:
                pass


def _apply_fit_frame(context):
    """贴合画框：让 3D 视图与摄像机画面框完全一致（所见即所得）。"""
    cam = _ensure_camera(context)
    scene = context.scene
    ok = False
    # 1. 清除渲染区域裁剪，渲染完整画面框
    try:
        scene.render.use_border = False
    except Exception:
        pass
    # 2. 传感器自动匹配输出分辨率比例（画面框比例 = 输出比例）
    if cam.data is not None:
        try:
            cam.data.sensor_fit = 'AUTO'
        except Exception:
            pass
    # 3. 框住摄像机边界：视图与画面框完全重合
    try:
        bpy.ops.view3d.view_center_camera()
        ok = True
    except Exception:
        ok = _frame_camera_bounds_manual(context, cam)
    return ok


def _frame_camera_bounds_manual(context, cam):
    """手动设置视图距离使视图与画面框重合（内置操作符不可用时的备用方案）。"""
    try:
        from math import atan, tan
        if cam.data is None:
            return False
        cam_fov_y = cam.data.angle_y
        if cam_fov_y <= 0.0:
            return False
        for area in context.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                r3d = getattr(space, "region_3d", None)
                if r3d is None:
                    continue
                lens_view = max(getattr(r3d, "lens", 50.0), 0.1)
                # 3D 视图按 32mm 传感器惯例计算纵向视场角：tan(fov/2) = 16/lens
                view_fov_y = 2.0 * atan(16.0 / lens_view)
                t_cam = tan(cam_fov_y / 2.0)
                t_view = tan(view_fov_y / 2.0)
                if t_view <= 0.0:
                    continue
                # 令视图平面高度 = 摄像机焦平面高度（焦距毫米 -> 米）
                d_view = (cam.data.lens * 0.001) * t_cam / t_view
                if d_view > 0.0001:
                    r3d.view_distance = d_view
                    return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# 场景设置
# ---------------------------------------------------------------------------

class QUICKANIM_Settings(PropertyGroup):
    camera_auto_key: BoolProperty(
        name="切帧自动注册",
        default=False,
        description="切换帧时自动为摄像机注册关键帧（配合 MMD 模式使用）",
    )
    recording: BoolProperty(
        name="录制中",
        default=False,
    )
    interval: FloatProperty(
        name="检测间隔(秒)",
        default=0.1,
        min=0.02,
        max=1.0,
        description="实时录制时检测摄像机移动的频率，越小越灵敏",
    )
    fit_on_mmd: BoolProperty(
        name="MMD 模式自动贴合画框",
        default=True,
        description="进入 MMD 模式/开始实时录制时，自动让视图与画面框完全一致",
    )


# ---------------------------------------------------------------------------
# 关键帧操作符
# ---------------------------------------------------------------------------

class QUICKANIM_OT_toggle_autokey(Operator):
    """开启/关闭自动记录关键帧（红点）。"""
    bl_idname = "quickanim.toggle_autokey"
    bl_label = "自动记录关键帧"
    bl_description = "开关自动关键帧：摆姿势时自动插帧"

    def execute(self, context):
        ts = context.scene.tool_settings
        ts.use_keyframe_insert_auto = not ts.use_keyframe_insert_auto
        state = "已开启" if ts.use_keyframe_insert_auto else "已关闭"
        self.report({'INFO'}, f"自动记录关键帧 {state}")
        return {'FINISHED'}


class QUICKANIM_OT_key_selected(Operator):
    """为选中的物体/骨骼插关键帧。"""
    bl_idname = "quickanim.key_selected"
    bl_label = "K 选中 (Loc/Rot/Scale)"
    bl_description = "在当前帧为选中的物体或骨骼插入 位置/旋转/缩放 关键帧"

    def execute(self, context):
        frame = context.scene.frame_current
        obj = context.object
        if _armature_in_pose_mode(context):
            for pb in _selected_pose_bones(obj):
                _key_pose_bone(pb, frame)
            self.report({'INFO'}, "已为选中骨骼插入关键帧")
        else:
            objs = context.selected_objects
            if not objs:
                self.report({'WARNING'}, "请先选中物体或进入姿态模式选择骨骼")
                return {'CANCELLED'}
            for o in objs:
                _key_object(o, frame)
            self.report({'INFO'}, f"已为 {len(objs)} 个物体插入关键帧")
        return {'FINISHED'}


class QUICKANIM_OT_key_whole_rig(Operator):
    """为整副骨架（全部骨骼）插关键帧。"""
    bl_idname = "quickanim.key_whole_rig"
    bl_label = "K 整副骨架"
    bl_description = "在当前帧为整副骨架的所有骨骼插入关键帧"

    def execute(self, context):
        frame = context.scene.frame_current
        obj = context.object
        if _armature_in_pose_mode(context):
            for pb in obj.pose.bones:
                _key_pose_bone(pb, frame)
            self.report({'INFO'}, "已为整副骨架插入关键帧")
            return {'FINISHED'}
        self.report({'WARNING'}, "请先选择骨架并进入姿态模式 (Tab)")
        return {'CANCELLED'}


class QUICKANIM_OT_copy_pose(Operator):
    """复制当前姿势到缓冲。"""
    bl_idname = "quickanim.copy_pose"
    bl_label = "复制姿势"
    bl_description = "复制当前选中骨骼的姿势"

    def execute(self, context):
        if not _armature_in_pose_mode(context):
            self.report({'WARNING'}, "请先进入姿态模式 (Tab)")
            return {'CANCELLED'}
        bpy.ops.pose.copy()
        self.report({'INFO'}, "姿势已复制")
        return {'FINISHED'}


class QUICKANIM_OT_paste_pose(Operator):
    """粘贴姿势。"""
    bl_idname = "quickanim.paste_pose"
    bl_label = "粘贴姿势"
    bl_description = "粘贴已复制的姿势"

    flipped: BoolProperty(name="镜像", default=False)

    def execute(self, context):
        if not _armature_in_pose_mode(context):
            self.report({'WARNING'}, "请先进入姿态模式 (Tab)")
            return {'CANCELLED'}
        bpy.ops.pose.paste(flipped=self.flipped)
        return {'FINISHED'}


class QUICKANIM_OT_make_loop(Operator):
    """让末帧姿势等于首帧姿势，实现循环动画。"""
    bl_idname = "quickanim.make_loop"
    bl_label = "循环 (首帧 = 末帧)"
    bl_description = "把首帧姿势复制到末帧并插关键帧，形成无缝循环"

    def execute(self, context):
        scene = context.scene
        start, end, cur = scene.frame_start, scene.frame_end, scene.frame_current
        obj = context.object

        if _armature_in_pose_mode(context):
            scene.frame_set(start)
            basis = {pb.name: pb.matrix_basis.copy() for pb in obj.pose.bones}
            scene.frame_set(end)
            for name, mat in basis.items():
                pb = obj.pose.bones.get(name)
                if pb:
                    pb.matrix_basis = mat
                    _key_pose_bone(pb, end)
        else:
            objs = context.selected_objects or list(context.visible_objects)
            if not objs:
                self.report({'WARNING'}, "没有可循环的物体")
                return {'CANCELLED'}
            scene.frame_set(start)
            snap = {}
            for o in objs:
                snap[o.name] = (
                    o.location.copy(),
                    o.rotation_euler.copy(),
                    o.rotation_quaternion.copy(),
                    o.scale.copy(),
                    o.rotation_mode,
                )
            scene.frame_set(end)
            for o in objs:
                if o.name not in snap:
                    continue
                loc, reuler, rquat, sc, rmode = snap[o.name]
                o.location = loc
                o.scale = sc
                if rmode == 'QUATERNION':
                    o.rotation_quaternion = rquat
                else:
                    o.rotation_euler = reuler
                _key_object(o, end)

        scene.frame_set(cur)
        self.report({'INFO'}, f"已把首帧({start})姿势复制到末帧({end})，可循环")
        return {'FINISHED'}


class QUICKANIM_OT_snap_keys(Operator):
    """把关键帧吸附到最近的整数帧。"""
    bl_idname = "quickanim.snap_keys"
    bl_label = "吸附到整数帧"
    bl_description = "把所有关键帧的时间对齐到最近的整数帧，去掉小数帧"

    def execute(self, context):
        fcus = _selected_fcurves(context)
        if not fcus:
            self.report({'WARNING'}, "没有可处理的动画曲线（请选中带动画的物体）")
            return {'CANCELLED'}
        count = 0
        for fcu in fcus:
            for kp in fcu.keyframe_points:
                new_x = round(kp.co.x)
                delta = new_x - kp.co.x
                kp.co.x = new_x
                kp.handle_left.x += delta
                kp.handle_right.x += delta
                count += 1
        self.report({'INFO'}, f"已对齐 {count} 个关键帧")
        return {'FINISHED'}


class QUICKANIM_OT_clean_keys(Operator):
    """删除近似共线的冗余关键帧。"""
    bl_idname = "quickanim.clean_keys"
    bl_label = "清理冗余帧"
    bl_description = "删除在直线上近似多余的关键帧，简化曲线"

    threshold: FloatProperty(
        name="容差", default=0.0001, min=0.0, max=10.0,
        description="值方向上的容差，越大删得越多",
    )

    def execute(self, context):
        fcus = _selected_fcurves(context)
        if not fcus:
            self.report({'WARNING'}, "没有可处理的动画曲线")
            return {'CANCELLED'}
        removed = 0
        for fcu in fcus:
            pts = fcu.keyframe_points
            i = 1
            while i < len(pts) - 1:
                p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
                span = p2.co.x - p0.co.x
                t = (p1.co.x - p0.co.x) / span if span != 0 else 0.0
                interp_y = p0.co.y + t * (p2.co.y - p0.co.y)
                if abs(p1.co.y - interp_y) <= self.threshold:
                    try:
                        fcu.keyframe_points.remove(p1, fast=True)
                    except TypeError:
                        fcu.keyframe_points.remove(p1)
                    removed += 1
                else:
                    i += 1
        self.report({'INFO'}, f"已删除 {removed} 个冗余关键帧")
        return {'FINISHED'}


class QUICKANIM_OT_ease(Operator):
    """一键设置缓动。"""
    bl_idname = "quickanim.ease"
    bl_label = "设置缓动"
    bl_description = "为关键帧设置缓动方式"

    mode: EnumProperty(
        name="缓动",
        items=[
            ('AUTO', "平滑 (Auto)", "自动钳制曲线，最常用"),
            ('LINEAR', "线性 (Linear)", "匀速，无缓动"),
            ('VECTOR', "折线 (Vector)", "转角不圆滑"),
        ],
        default='AUTO',
    )

    def execute(self, context):
        fcus = _selected_fcurves(context)
        if not fcus:
            self.report({'WARNING'}, "没有可处理的动画曲线")
            return {'CANCELLED'}
        htype = {
            'AUTO': 'AUTO_CLAMPED',
            'LINEAR': 'LINEAR',
            'VECTOR': 'VECTOR',
        }[self.mode]
        count = 0
        for fcu in fcus:
            for kp in fcu.keyframe_points:
                kp.handle_left_type = htype
                kp.handle_right_type = htype
                count += 1
        self.report({'INFO'}, f"已为 {count} 个关键帧设置 {self.mode} 缓动")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 摄像机（MMD 风格）操作符
# ---------------------------------------------------------------------------

class QUICKANIM_OT_ensure_camera(Operator):
    """选中/创建摄像机。"""
    bl_idname = "quickanim.ensure_camera"
    bl_label = "选中摄像机"
    bl_description = "确保场景有激活摄像机（没有则新建），并设为当前激活摄像机"

    def execute(self, context):
        cam = _ensure_camera(context)
        self.report({'INFO'}, f"激活摄像机：{cam.name}")
        return {'FINISHED'}


class QUICKANIM_OT_key_camera(Operator):
    """为摄像机注册关键帧。"""
    bl_idname = "quickanim.key_camera"
    bl_label = "注册关键帧 (K 摄像机)"
    bl_description = "在当前帧为摄像机的位置/旋转/焦距注册关键帧（MMD 的 Register）"

    def execute(self, context):
        cam = _ensure_camera(context)
        _key_camera(cam, context.scene.frame_current)
        self.report({'INFO'}, f"已注册摄像机关键帧 @ 帧 {context.scene.frame_current}")
        return {'FINISHED'}


class QUICKANIM_OT_fit_frame(Operator):
    """贴合画框：让视图与摄像机画面框完全一致。"""
    bl_idname = "quickanim.fit_frame"
    bl_label = "贴合画框"
    bl_description = "让 3D 视图与摄像机画面框完全一致（所见即所得）：清除渲染区域裁剪、传感器匹配输出比例、视图框住画框"

    def execute(self, context):
        _set_view_to_camera(context, lock=False)
        ok = _apply_fit_frame(context)
        if ok:
            self.report({'INFO'}, "已贴合画框：视图 = 摄像机画面框")
        else:
            self.report({'INFO'}, "画框已对齐（若仍有灰边可按 Home 键）")
        return {'FINISHED'}


class QUICKANIM_OT_mmd_mode(Operator):
    """进入/退出 MMD 摄像机模式。"""
    bl_idname = "quickanim.mmd_mode"
    bl_label = "MMD 摄像机模式"
    bl_description = "像 MMD 一样：视图即摄像机，拖动视角(旋转/平移/缩放)直接移动摄像机"

    def execute(self, context):
        locked = False
        for area in context.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type == 'VIEW_3D' and getattr(space, "lock_camera", False):
                    locked = True
        if locked:
            _set_view_to_camera(context, lock=False)
            self.report({'INFO'}, "已退出 MMD 摄像机模式")
        else:
            _ensure_camera(context)
            _set_view_to_camera(context, lock=True)
            if context.scene.quickanim.fit_on_mmd:
                _apply_fit_frame(context)
            self.report({'INFO'}, "已进入 MMD 模式：拖动视角即移动摄像机，画框已贴合")
        return {'FINISHED'}


# ---- 实时录制（随摄像机移动自动 K 帧）----

_rec_timer = None
_rec_cam = None
_rec_last_key = None


def _stop_recording():
    global _rec_timer, _rec_cam, _rec_last_key
    if _rec_timer is not None:
        try:
            bpy.app.timers.unregister(_rec_timer)
        except Exception:
            pass
    _rec_timer = None
    _rec_cam = None
    _rec_last_key = None


def _record_tick():
    """定时器回调：检测摄像机是否移动，移动则自动注册关键帧。"""
    global _rec_last_key
    cam = _rec_cam
    if cam is None:
        _stop_recording()
        return None
    scene = None
    try:
        scene = bpy.context.scene
    except Exception:
        scene = None
    if scene is None:
        _stop_recording()
        return None
    settings = getattr(scene, "quickanim", None)
    if settings is None or not settings.recording:
        _stop_recording()
        return None
    try:
        loc = tuple(round(v, 4) for v in cam.location)
        rot = tuple(round(v, 4) for v in cam.rotation_euler)
        lens = round(cam.data.lens, 2) if cam.data else 0.0
    except Exception:
        _stop_recording()
        return None
    key = (loc, rot, lens)
    if key != _rec_last_key:
        _key_camera(cam, scene.frame_current)
        _rec_last_key = key
    return settings.interval


def _start_recording(context):
    global _rec_timer, _rec_cam, _rec_last_key
    _stop_recording()
    cam = _ensure_camera(context)
    _rec_cam = cam
    _rec_last_key = None
    _rec_timer = bpy.app.timers.register(_record_tick, first_interval=0.05)


class QUICKANIM_OT_record_camera(Operator):
    """开始/停止摄像机实时录制。"""
    bl_idname = "quickanim.record_camera"
    bl_label = "实时录制摄像机"
    bl_description = "开启后：进入 MMD 模式并实时检测，移动摄像机就自动注册关键帧"

    def execute(self, context):
        settings = context.scene.quickanim
        if settings.recording:
            _stop_recording()
            settings.recording = False
            self.report({'INFO'}, "摄像机录制已停止")
        else:
            _set_view_to_camera(context, lock=True)
            if settings.fit_on_mmd:
                _apply_fit_frame(context)
            _start_recording(context)
            settings.recording = True
            self.report({'INFO'}, "实时录制中：移动视角即自动 K 帧，再次点击停止")
        return {'FINISHED'}


@persistent
def _on_frame_change(scene):
    """切帧自动注册摄像机关键帧。"""
    settings = getattr(scene, "quickanim", None)
    if settings is None or not settings.camera_auto_key:
        return
    cam = scene.camera
    if cam is None:
        return
    _key_camera(cam, scene.frame_current)


# ---------------------------------------------------------------------------
# 渲染操作符
# ---------------------------------------------------------------------------

class QUICKANIM_OT_set_output_png(Operator):
    """输出为 PNG 序列。"""
    bl_idname = "quickanim.set_output_png"
    bl_label = "输出 PNG 序列"
    bl_description = "渲染为 PNG 图片序列（推荐：可断点续渲，不怕崩溃）"

    def execute(self, context):
        scene = context.scene
        ims = scene.render.image_settings
        try:
            # Blender 5.0+：先选媒体类型 IMAGE，再选 PNG
            if hasattr(ims, "media_type"):
                ims.media_type = 'IMAGE'
            ims.file_format = 'PNG'
            ims.color_mode = 'RGBA'
            ims.compression = 15
        except Exception:
            self.report({'WARNING'}, "设置 PNG 输出失败")
            return {'CANCELLED'}
        self.report({'INFO'}, "输出格式已设为 PNG 序列")
        return {'FINISHED'}


class QUICKANIM_OT_set_output_mp4(Operator):
    """输出为 MP4 视频。"""
    bl_idname = "quickanim.set_output_mp4"
    bl_label = "输出 MP4 视频"
    bl_description = "渲染为 MP4(H.264) 视频，一步到位"

    def execute(self, context):
        scene = context.scene
        ims = scene.render.image_settings
        try:
            # Blender 5.0+：先选媒体类型 VIDEO，再选 FFMPEG
            if hasattr(ims, "media_type"):
                ims.media_type = 'VIDEO'
            ims.file_format = 'FFMPEG'
        except Exception:
            self.report({'WARNING'}, "此版本 Blender 无法直接输出视频，请用 PNG 序列后在视频序列编辑器合成")
            return {'CANCELLED'}
        try:
            scene.render.ffmpeg.format = 'MPEG4'
            scene.render.ffmpeg.codec = 'H264'
            scene.render.ffmpeg.constant_rate_factor = 'MEDIUM'
            scene.render.ffmpeg.audio_codec = 'NONE'
        except Exception:
            pass
        self.report({'INFO'}, "输出格式已设为 MP4 (H.264)")
        return {'FINISHED'}


class QUICKANIM_OT_preset(Operator):
    """一键画质预设。"""
    bl_idname = "quickanim.preset"
    bl_label = "画质预设"
    bl_description = "应用一套快速预览/草稿/成片的渲染参数"

    preset: EnumProperty(
        name="预设",
        items=[
            ('PREVIEW', "快速预览", "EEVEE，低分辨率，秒出小样"),
            ('DRAFT', "草稿", "Cycles 低采样 + 降噪"),
            ('FINAL', "成片", "Cycles 高采样 + 降噪"),
        ],
    )

    def execute(self, context):
        scene = context.scene
        if self.preset == 'PREVIEW':
            scene.render.engine = 'BLENDER_EEVEE'
            scene.render.resolution_percentage = 50
            try:
                scene.eevee.taa_render_samples = 32
            except Exception:
                pass
        elif self.preset == 'DRAFT':
            scene.render.engine = 'CYCLES'
            scene.cycles.samples = 128
            try:
                scene.cycles.use_denoising = True
            except Exception:
                pass
            scene.render.resolution_percentage = 100
        elif self.preset == 'FINAL':
            scene.render.engine = 'CYCLES'
            scene.cycles.samples = 512
            try:
                scene.cycles.use_denoising = True
            except Exception:
                pass
            scene.render.resolution_percentage = 100
        self.report({'INFO'}, f"已应用预设：{self.preset}")
        return {'FINISHED'}


class QUICKANIM_OT_use_gpu(Operator):
    """切换 GPU 渲染。"""
    bl_idname = "quickanim.use_gpu"
    bl_label = "切换 GPU 渲染"
    bl_description = "Cycles 切换到 GPU 并启用可用显卡（NVIDIA/Mac/AMD）"

    def execute(self, context):
        scene = context.scene
        try:
            scene.cycles.device = 'GPU'
        except Exception:
            pass

        cprefs = None
        addon = context.preferences.addons.get('cycles')
        if addon:
            cprefs = addon.preferences
        if cprefs is None:
            self.report({'WARNING'}, "未找到 Cycles 渲染器偏好")
            return {'CANCELLED'}

        if sys.platform == 'darwin':
            candidates = ['METAL', 'OPTIX', 'CUDA', 'OPENCL', 'HIP', 'ONEAPI']
        else:
            candidates = ['OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL', 'OPENCL']

        picked = None
        for ct in candidates:
            try:
                cprefs.compute_device_type = ct
                picked = ct
                break
            except Exception:
                continue
        try:
            cprefs.refresh_devices()
            for d in cprefs.devices:
                try:
                    d.use = True
                except Exception:
                    pass
        except Exception:
            pass

        msg = f"已切换 GPU 渲染 (设备类型: {picked})" if picked else "已切换 GPU 渲染"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class QUICKANIM_OT_set_output_dir(Operator):
    """选择输出目录。"""
    bl_idname = "quickanim.set_output_dir"
    bl_label = "选择输出目录"
    bl_description = "选择渲染结果保存的文件夹"

    filepath: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        d = os.path.dirname(self.filepath.rstrip(os.sep))
        if not os.path.isdir(d):
            d = self.filepath
        context.scene.render.filepath = os.path.join(d, context.scene.name + "_")
        self.report({'INFO'}, f"输出目录：{d}")
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class QUICKANIM_OT_render_still(Operator):
    """渲染当前帧到文件。"""
    bl_idname = "quickanim.render_still"
    bl_label = "渲染当前帧"
    bl_description = "按当前设置渲染单帧并保存到输出目录"

    def execute(self, context):
        if not context.scene.render.filepath:
            self.report({'WARNING'}, "请先设置输出目录")
            return {'CANCELLED'}
        bpy.ops.render.render(write_still=True)
        self.report({'INFO'}, "单帧渲染完成")
        return {'FINISHED'}


class QUICKANIM_OT_render_animation(Operator):
    """渲染动画。"""
    bl_idname = "quickanim.render_animation"
    bl_label = "渲染动画"
    bl_description = "渲染整个帧范围（建议先渲染单帧确认效果）"

    def execute(self, context):
        if not context.scene.render.filepath:
            self.report({'WARNING'}, "请先设置输出目录")
            return {'CANCELLED'}
        bpy.ops.render.render(animation=True, write_still=True)
        self.report({'INFO'}, "动画渲染完成")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 偏好设置
# ---------------------------------------------------------------------------

class QUICKANIM_AP_preferences(AddonPreferences):
    bl_idname = __name__

    default_dir: StringProperty(
        name="默认输出目录",
        subtype='DIR_PATH',
        default="//render//",
        description="默认渲染输出目录（// 表示当前 .blend 文件所在目录）",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "default_dir")


# ---------------------------------------------------------------------------
# 面板
# ---------------------------------------------------------------------------

class QUICKANIM_PT_main(Panel):
    bl_label = "QuickAnim 快速动画"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "QuickAnim"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        ts = scene.tool_settings
        settings = scene.quickanim

        # ---- K 帧 ----
        box = layout.box()
        box.label(text="关键帧", icon='KEYINGSET')

        row = box.row(align=True)
        if ts.use_keyframe_insert_auto:
            row.operator("quickanim.toggle_autokey", text="自动关键帧：开启", icon='CHECKBOX_HLT')
        else:
            row.operator("quickanim.toggle_autokey", text="自动关键帧：关闭", icon='CHECKBOX_DEHLT')

        box.operator("quickanim.key_selected", icon='DECORATE_KEYFRAME')
        box.operator("quickanim.key_whole_rig", icon='ARMATURE_DATA')

        box.separator()
        row = box.row(align=True)
        row.operator("quickanim.copy_pose", text="复制姿势", icon='COPYDOWN')
        row.operator("quickanim.paste_pose", text="粘贴", icon='PASTEDOWN').flipped = False
        row.operator("quickanim.paste_pose", text="镜像粘贴", icon='MOD_MIRROR').flipped = True

        box.operator("quickanim.make_loop", icon='FILE_REFRESH')

        box.separator()
        row = box.row(align=True)
        row.operator("quickanim.snap_keys", text="吸附整数帧")
        op = row.operator("quickanim.clean_keys", text="清理冗余帧")
        op.threshold = 0.0001

        row = box.row(align=True)
        e1 = row.operator("quickanim.ease", text="平滑")
        e1.mode = 'AUTO'
        e2 = row.operator("quickanim.ease", text="线性")
        e2.mode = 'LINEAR'

        # ---- 摄像机（MMD 风格）----
        box = layout.box()
        box.label(text="摄像机 (MMD 风格)", icon='VIEW_CAMERA')

        row = box.row(align=True)
        row.operator("quickanim.ensure_camera", text="选中摄像机", icon='CAMERA_DATA')
        row.operator("quickanim.mmd_mode", text="MMD 模式", icon='VIEW_CAMERA')
        row.operator("quickanim.fit_frame", text="贴合画框", icon='BORDER_RECT')

        box.operator("quickanim.key_camera", icon='DECORATE_KEYFRAME')

        row = box.row(align=True)
        if settings.recording:
            row.operator("quickanim.record_camera", text="停止录制", icon='CANCEL')
        else:
            row.operator("quickanim.record_camera", text="开始实时录制", icon='REC')
        row.prop(settings, "interval", text="间隔")

        box.prop(settings, "camera_auto_key", text="切帧自动注册")
        box.prop(settings, "fit_on_mmd", text="MMD/录制时自动贴合画框")

        # ---- 渲染 ----
        box = layout.box()
        box.label(text="渲染", icon='RESTRICT_RENDER_OFF')

        row = box.row(align=True)
        row.operator("quickanim.preset", text="快速预览").preset = 'PREVIEW'
        row.operator("quickanim.preset", text="草稿").preset = 'DRAFT'
        row.operator("quickanim.preset", text="成片").preset = 'FINAL'

        box.prop(scene.render, "engine", text="渲染器")

        row = box.row(align=True)
        row.operator("quickanim.set_output_png", text="PNG 序列", icon='FILE_IMAGE')
        row.operator("quickanim.set_output_mp4", text="MP4 视频", icon='FILE_MOVIE')

        box.prop(scene.render, "resolution_percentage", text="分辨率 %")

        if scene.render.engine == 'CYCLES':
            box.prop(scene.cycles, "samples", text="采样")
            try:
                box.prop(scene.cycles, "use_denoising", text="降噪")
            except Exception:
                pass
            box.operator("quickanim.use_gpu", icon='PROP_CON')

        box.separator()
        box.operator("quickanim.set_output_dir", icon='FILE_FOLDER')
        box.label(text=f"输出：{scene.render.filepath or '(未设置)'}")
        row = box.row(align=True)
        row.operator("quickanim.render_still", icon='RENDER_STILL')
        row.operator("quickanim.render_animation", icon='RENDER_ANIMATION')


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

classes = (
    QUICKANIM_Settings,
    QUICKANIM_OT_toggle_autokey,
    QUICKANIM_OT_key_selected,
    QUICKANIM_OT_key_whole_rig,
    QUICKANIM_OT_copy_pose,
    QUICKANIM_OT_paste_pose,
    QUICKANIM_OT_make_loop,
    QUICKANIM_OT_snap_keys,
    QUICKANIM_OT_clean_keys,
    QUICKANIM_OT_ease,
    QUICKANIM_OT_ensure_camera,
    QUICKANIM_OT_key_camera,
    QUICKANIM_OT_fit_frame,
    QUICKANIM_OT_mmd_mode,
    QUICKANIM_OT_record_camera,
    QUICKANIM_OT_set_output_png,
    QUICKANIM_OT_set_output_mp4,
    QUICKANIM_OT_preset,
    QUICKANIM_OT_use_gpu,
    QUICKANIM_OT_set_output_dir,
    QUICKANIM_OT_render_still,
    QUICKANIM_OT_render_animation,
    QUICKANIM_AP_preferences,
    QUICKANIM_PT_main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.quickanim = PointerProperty(type=QUICKANIM_Settings)
    bpy.app.handlers.frame_change_post.append(_on_frame_change)


def unregister():
    _stop_recording()
    try:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change)
    except Exception:
        pass
    try:
        del bpy.types.Scene.quickanim
    except Exception:
        pass
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
