"""拍照识药路由：上传药物图片/说明书照片或短视频，调用视觉模型分析。"""
import base64
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import prompts
from ..llm import encode_image, get_provider
from .deps import get_current_user

router = APIRouter(prefix="/api/vision", tags=["vision"])

MAX_SIZE = 8 * 1024 * 1024
VIDEO_MAX_SIZE = 32 * 1024 * 1024
ALLOWED = ("image/jpeg", "image/png", "image/webp", "image/jpg", "application/octet-stream")
VIDEO_EXT = (".mp4", ".mov", ".webm", ".avi")


def _pick_provider():
    """优先选择配置了视觉模型且可用的 provider。"""
    for pid in ("zhipu", "openai", "deepseek"):
        p = get_provider(pid)
        if p and p.available() and p.vision_model():
            return p
    return None


def _extract_section(md: str, heading: str) -> str:
    """从 markdown 文本里抓取某 `**heading**` 段落到下一个 - ** 项或文末。"""
    m = re.search(
        rf"\*?\*?{re.escape(heading)}\*?\*?\s*[:：]?\s*(.+?)(?=\n\s*-\s*\*\*|\Z)",
        md, re.S)
    return (m.group(1).strip() if m else "").strip()


def _build_response(result: str, provider) -> dict:
    """从模型 markdown 文本中抽取「不可混合使用的药物」与「潜在风险」段落。"""
    interactions = _extract_section(result, "不可混合使用的药物")
    risks = _extract_section(result, "潜在风险")
    blurry = "模糊" in result or "无法识别" in result
    return {"result": result, "model": provider.vision_model(),
            "interactions": interactions, "risks": risks,
            "needs_retake": blurry}


@router.post("/medicine")
async def recognize_medicine(file: UploadFile = File(...),
                             user: dict = Depends(get_current_user)):
    """图片识药：单张图片直接送视觉模型，返回结果与不可混用/风险字段。"""
    content = await file.read()
    if not content:
        raise HTTPException(400, "请上传图片")
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "图片超过 8MB 限制")

    provider = _pick_provider()
    if provider is None:
        raise HTTPException(
            400, "当前没有可用的视觉模型（需在 config.yaml 中配置已开通视觉的模型）")

    b64 = encode_image(content)
    try:
        result = await provider.vision(b64, prompts.MEDICINE_PROMPT)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"识药服务调用失败：{str(e)[:200]}")

    return _build_response(result, provider)


# ---------------- 视频识药 ----------------

def _frame_to_jpeg(frame) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return b""
    return buf.tobytes()


def _extract_frames(content: bytes, max_frames: int = 4) -> list[bytes]:
    """用 opencv 从短视频中均匀抽取若干关键帧为 JPEG（bytes）。

    opencv 自带 ffmpeg 后端，无需系统 ffmpeg。失败或无可用帧时返回 []。
    """
    try:
        import cv2  # noqa: F401
    except Exception:
        return []
    import os
    import tempfile

    frames: list[bytes] = []
    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        cap = cv2.VideoCapture(tmp)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            # 某些封装下帧数读取失败，退化为逐帧读直到取够或结束
            while len(frames) < max_frames:
                ok, frame = cap.read()
                if not ok:
                    break
                frames.append(_frame_to_jpeg(frame))
                for _ in range(10):  # 跳过若干帧避免重复
                    if not cap.grab():
                        break
        else:
            step = max(1, total // max_frames)
            idxs = [min(total - 1, i * step) for i in range(max_frames)]
            for fi in idxs:
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ok, frame = cap.read()
                if ok:
                    frames.append(_frame_to_jpeg(frame))
        cap.release()
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
    return [f for f in frames if f][:max_frames]


@router.post("/medicine-video")
async def recognize_medicine_video(file: UploadFile = File(...),
                                    user: dict = Depends(get_current_user)):
    """视频识药：从短视频抽取关键帧，逐帧送视觉模型识别，再聚合出结论。

    - 抽取最多 4 帧以控制耗时与 token；
    - 取首个能识别出药名/内容的帧结果作为主结论，其余帧信息并入 frames 观察列表；
    - 同样解析「不可混合使用药物」与「潜在风险」。
    """
    name = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()
    if not (name.endswith(VIDEO_EXT) or ctype.startswith("video/")):
        raise HTTPException(400, "请上传视频文件（mp4/mov/webm/avi）")
    content = await file.read()
    if not content:
        raise HTTPException(400, "视频为空")
    if len(content) > VIDEO_MAX_SIZE:
        raise HTTPException(400, "视频超过 32MB 限制，请裁剪后再上传")

    provider = _pick_provider()
    if provider is None:
        raise HTTPException(400, "当前没有可用的视觉模型，无法进行视频识药")

    frames = _extract_frames(content, max_frames=4)
    if not frames:
        raise HTTPException(400, "无法从视频中抽取有效画面帧，请改用图片上传或更换视频")

    frame_results: list[dict] = []
    main: str | None = None
    for i, fbytes in enumerate(frames):
        b64 = base64.b64encode(fbytes).decode()
        try:
            txt = await provider.vision(b64, prompts.MEDICINE_PROMPT)
        except Exception as e:
            frame_results.append({"frame": i + 1, "error": str(e)[:160]})
            continue
        blurry = "模糊" in txt or "无法识别" in txt
        frame_results.append({"frame": i + 1, "ok": not blurry,
                              "snippet": txt[:200], "blurry": blurry})
        if main is None and not blurry:
            main = txt  # 首个能识别出内容的帧作为主结论

    if main is None:
        fallback = "视频中各帧均无法清晰识别药物，请重新拍摄包含药盒名称或说明书文字的视频。"
        return {"result": fallback, "model": provider.vision_model(),
                "interactions": "", "risks": "",
                "frames": frame_results, "frame_count": len(frames),
                "needs_retake": True}

    resp = _build_response(main, provider)
    resp["frames"] = frame_results
    resp["frame_count"] = len(frames)
    return resp
