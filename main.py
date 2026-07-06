"""
Image Dimension Annotator — FastAPI service.

    POST /annotate?width=30&height=45&unit=cm      + image
    GET  /annotate?width=30&height=45&image_url=... (browser-friendly)

Image can be supplied three ways (POST):
    1. ?image_url=https://...          (server fetches it)
    2. multipart form field  file=@photo.jpg
    3. raw request body with an image/* Content-Type

All drawing options are query params (see /  for the list). Response is the
annotated image bytes (image/png by default); use response=base64|dataurl to
get JSON instead — handy for Make.com / low-code tools.
"""

from __future__ import annotations

import base64
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError

from annotate import DimStyle, annotate, open_image, to_bytes

app = FastAPI(title="Image Dimension Annotator", version="1.0.0")

MAX_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 20 * 1024 * 1024))
ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE_URLS", "").lower() in ("1", "true", "yes")
FETCH_TIMEOUT = float(os.environ.get("FETCH_TIMEOUT", "15"))


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Image sourcing
# ---------------------------------------------------------------------------
def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


async def fetch_url(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ApiError(400, "image_url must start with http:// or https://")
    if not ALLOW_PRIVATE and not _host_is_public(parsed.hostname or ""):
        raise ApiError(400, "Refusing to fetch a private/loopback address "
                            "(set ALLOW_PRIVATE_URLS=1 to allow)")
    try:
        async with httpx.AsyncClient(follow_redirects=True,
                                     timeout=FETCH_TIMEOUT) as client:
            r = await client.get(url, headers={"User-Agent": "dimension-annotator/1.0"})
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise ApiError(502, f"Failed to fetch image_url: {e}")
    data = r.content
    if len(data) > MAX_BYTES:
        raise ApiError(413, "Fetched image exceeds size limit")
    return data


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
def _build_style(params: dict) -> tuple[float, float, DimStyle]:
    def get(name, default=None):
        v = params.get(name)
        return v if v not in (None, "") else default

    if get("width") is None or get("height") is None:
        raise ApiError(400, "width and height are required")
    try:
        width_value = float(get("width"))
        height_value = float(get("height"))
    except (TypeError, ValueError):
        raise ApiError(400, "width and height must be numbers")

    def as_int(name):
        v = get(name)
        return int(v) if v is not None else None

    try:
        style = DimStyle(
            unit=(get("unit", "") or ""),
            color=get("color", "#151515"),
            bg=get("bg", "#FFFFFF"),
            mode=get("mode", "inset"),
            halo=get("halo", "auto"),
            scale=float(get("scale", 1.0)),
            line_width=as_int("line_width"),
            font_size=as_int("font_size"),
            arrow_size=as_int("arrow_size"),
            gap=as_int("gap"),
            margin_extra=int(get("margin_extra", 0)),
            show_witness=str(get("witness", "1")).lower() not in ("0", "false", "no"),
            watermark=str(get("watermark", "")).lower() in ("1", "true", "yes", "on"),
            watermark_opacity=float(get("watermark_opacity", 0.12)),
            watermark_scale=float(get("watermark_scale", 0.72)),
            watermark_bg=get("watermark_bg", "#FFFFFF"),
        )
    except (TypeError, ValueError):
        raise ApiError(400, "A numeric styling parameter was not a number")
    return width_value, height_value, style


def _decode(image_bytes: bytes) -> Image.Image:
    if len(image_bytes) > MAX_BYTES:
        raise ApiError(413, "Image exceeds size limit")
    try:
        return ImageOps.exif_transpose(open_image(image_bytes))
    except Image.DecompressionBombError:
        raise ApiError(413, "Image has too many pixels")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ApiError(422, "Could not decode the image data")


def _render(result: Image.Image, params: dict, style: DimStyle):
    fmt = (params.get("format") or "png").lower()
    data, media = to_bytes(result, fmt=fmt, bg=style.bg)
    mode = (params.get("response") or "binary").lower()
    if mode in ("base64", "dataurl"):
        b64 = base64.b64encode(data).decode()
        if mode == "dataurl":
            b64 = f"data:{media};base64,{b64}"
        return JSONResponse({"image": b64, "media_type": media,
                             "width": result.size[0], "height": result.size[1]})
    headers = {}
    if params.get("download"):
        ext = "jpg" if media == "image/jpeg" else media.split("/")[-1]
        headers["Content-Disposition"] = f'attachment; filename="annotated.{ext}"'
    return Response(content=data, media_type=media, headers=headers)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {
        "service": "Image Dimension Annotator",
        "draws": "width along the bottom, height up the left (arrows + labels)",
        "endpoints": {
            "POST /annotate": "options as query params; image via image_url, "
                              "multipart 'file', or a raw image body",
            "GET /annotate": "options as query params; image via image_url only",
        },
        "required": ["width", "height"],
        "options": {
            "unit": "label suffix, e.g. cm (default none)",
            "mode": "inset = same output size, drawn on the image (default) | "
                    "margin = adds a white border around the image",
            "halo": "inset contrast outline: auto | colour | none (default auto)",
            "color": "line/label colour, name or hex (default #151515)",
            "bg": "margin colour, margin mode only (default #FFFFFF)",
            "watermark": "1 to place the NAWAQIS logo BEHIND the image "
                         "(shows through the product's transparent areas)",
            "watermark_opacity": "0-1 logo opacity (default 0.12)",
            "watermark_scale": "logo width as a fraction of the image (default 0.72)",
            "watermark_bg": "colour behind transparent areas (default #FFFFFF)",
            "format": "png | jpeg | webp (default png)",
            "response": "binary | base64 | dataurl (default binary)",
            "scale": "multiply all sizes (default 1.0)",
            "font_size / line_width / arrow_size / gap": "explicit px overrides",
            "witness": "0 to hide the extension lines",
            "download": "1 to force a file download",
        },
        "example": "POST /annotate?width=30&height=45&unit=cm  with an image",
    }


@app.post("/annotate")
async def annotate_post(request: Request):
    try:
        params = dict(request.query_params)
        image_bytes = None
        content_type = request.headers.get("content-type", "")

        if content_type.startswith(("multipart/form-data",
                                    "application/x-www-form-urlencoded")):
            form = await request.form()
            for k, v in form.items():
                if k != "file" and isinstance(v, str):
                    params.setdefault(k, v)
            upload = form.get("file")
            if upload is not None and hasattr(upload, "read"):
                image_bytes = await upload.read()

        image_url = params.get("image_url")
        if not image_bytes and image_url:
            image_bytes = await fetch_url(image_url)

        if not image_bytes:
            body = await request.body()
            if body:
                image_bytes = body

        if not image_bytes:
            raise ApiError(400, "Provide an image via multipart 'file', "
                                "'image_url', or a raw image request body")

        width_value, height_value, style = _build_style(params)
        src = _decode(image_bytes)
        result = annotate(src, width_value, height_value, style)
        return _render(result, params, style)

    except ApiError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)
    except Exception as e:  # noqa: BLE001 - last-resort guard
        return JSONResponse({"error": f"Unexpected error: {e}"}, status_code=500)


@app.get("/annotate")
async def annotate_get(request: Request):
    try:
        params = dict(request.query_params)
        image_url = params.get("image_url")
        if not image_url:
            raise ApiError(400, "GET /annotate needs image_url "
                                "(use POST to upload binary or a file)")
        width_value, height_value, style = _build_style(params)
        image_bytes = await fetch_url(image_url)
        src = _decode(image_bytes)
        result = annotate(src, width_value, height_value, style)
        return _render(result, params, style)
    except ApiError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Unexpected error: {e}"}, status_code=500)
