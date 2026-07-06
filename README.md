# Image Dimension Annotator

A small HTTP API that takes an image (URL or binary) plus a **width** and
**height**, and returns the same image with engineering-style dimension lines
drawn on it:

- **Width** → a horizontal dimension line along the **bottom** (arrows + label)
- **Height** → a vertical dimension line up the **left** (arrows + label)

By default (**`mode=inset`**) the lines are drawn **on** the image just inside
the edges, so the output keeps the **exact same dimensions as the input**
(1024×1024 → 1024×1024). A contrast **halo** keeps them readable over any photo.
Set **`mode=margin`** to instead place the dimensions in added white margins
(larger output). Font size, line weight and arrow size auto-scale with the
image resolution.

```
     +---------------+
     |               |
  ^  |               |
  |  |    IMAGE      |
 24  |               |     <- height, up the left
  |  |               |
  v  +---------------+
     <----- 18 ----->      <- width, along the bottom
```

## Run it

```bash
cd image-dimension-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

> **No `venv` on your machine?** If `python3 -m venv` reports that `ensurepip`
> is unavailable, either install it once with `sudo apt install python3.12-venv`,
> or skip the venv and install into your user site instead:
> `pip install --user --break-system-packages -r requirements.txt`
> (then start it with `python3 -m uvicorn main:app --reload --port 8000`).

Open <http://localhost:8000/> for the self-describing help payload.

### Quick visual check (no server needed)

```bash
python examples/demo.py      # writes examples/demo_out.png
```

## Endpoints

### `POST /annotate`

Drawing options go in the **query string**; the image comes in **one** of three
ways:

| Image source        | How                                                        |
|---------------------|------------------------------------------------------------|
| Remote URL          | `?image_url=https://.../photo.jpg`                         |
| Multipart upload    | form field `file=@photo.jpg`                               |
| Raw binary body     | `--data-binary @photo.jpg` + `Content-Type: image/jpeg`    |

```bash
# 1) from a URL
curl -X POST "http://localhost:8000/annotate?width=30&height=45&unit=cm&image_url=https://picsum.photos/600/800" -o out.png

# 2) multipart file upload
curl -X POST "http://localhost:8000/annotate?width=30&height=45&unit=cm" \
     -F file=@photo.jpg -o out.png

# 3) raw binary body
curl -X POST "http://localhost:8000/annotate?width=30&height=45&unit=cm" \
     --data-binary @photo.jpg -H "Content-Type: image/jpeg" -o out.png
```

### `GET /annotate`

Same options, image via `image_url` only — convenient for a browser or an
`<img src>`:

```
http://localhost:8000/annotate?width=30&height=45&unit=cm&image_url=https://picsum.photos/600/800
```

## Parameters

| Param         | Required | Default   | Notes                                          |
|---------------|----------|-----------|------------------------------------------------|
| `width`       | ✅       | —         | Number drawn on the bottom line                |
| `height`      | ✅       | —         | Number drawn on the left line                  |
| `unit`        |          | *(none)*  | Suffix for both labels, e.g. `cm`              |
| `mode`        |          | `inset`   | `inset` = same output size, drawn on image · `margin` = adds a white border |
| `halo`        |          | `auto`    | Inset contrast outline: `auto` \| colour \| `none` |
| `color`       |          | `#151515` | Line + label colour (name or hex)              |
| `bg`          |          | `#FFFFFF` | Margin colour (**`margin` mode only**)         |
| `watermark`   |          | off       | `1` places the NAWAQIS logo **behind** the image (shows through transparent areas) |
| `watermark_opacity` |    | `0.12`    | Logo opacity, 0–1                              |
| `watermark_scale` |      | `0.72`    | Logo width as a fraction of the image          |
| `watermark_bg` |         | `#FFFFFF` | Colour behind the product's transparent areas  |
| `format`      |          | `png`     | `png` \| `jpeg` \| `webp`                      |
| `response`    |          | `binary`  | `binary` \| `base64` \| `dataurl` (JSON out)   |
| `scale`       |          | `1.0`     | Multiply all derived sizes                     |
| `font_size`   |          | auto      | Explicit label size in px                      |
| `line_width`  |          | auto      | Explicit line weight in px                     |
| `arrow_size`  |          | auto      | Explicit arrowhead length in px                |
| `gap`         |          | auto      | Gap between the image edge and the line        |
| `margin_extra`|          | `0`       | Extra outer breathing space in px              |
| `witness`     |          | `1`       | `0` hides the short extension lines            |
| `download`    |          | —         | `1` forces a file-download response            |

## Watermark (NAWAQIS logo behind the product)

Pass `watermark=1` to composite the NAWAQIS logo as a faint backdrop **behind**
the product — the classic catalog look. It shows through wherever the product
image is **transparent** (a cut-out PNG); on an opaque photo the product simply
covers it. Tune with `watermark_opacity` (default `0.12`) and `watermark_scale`
(default `0.72`). The logo ships in `assets/nawaqis-logo.png` and can be
overridden with the `WATERMARK_LOGO` env var.

```bash
curl -X POST "http://localhost:8000/annotate?width=8&height=22&unit=cm&watermark=1" \
     -F file=@product_cutout.png -o out.png
```

## Using it from Make.com / low-code

The easiest path: an **HTTP → Make a request** module.

- **URL**: `https://<your-host>/annotate?width={{width}}&height={{height}}&unit=cm&image_url={{product_image_url}}`
- **Method**: `POST` (or `GET`)
- Set `response=base64` (or `dataurl`) if you want the result as a JSON string
  to drop straight into another module; otherwise you get raw image bytes you
  can upload/attach.

## Deploy

**Docker** (bundles the DejaVu font):

```bash
docker build -t dimension-annotator .
docker run -p 8080:8080 dimension-annotator
```

The image listens on `$PORT` (default `8080`), so it runs as-is on **Cloud
Run**, **Railway**, **Render**, Fly.io, etc.

## Configuration (env vars)

| Var                  | Default | Purpose                                             |
|----------------------|---------|-----------------------------------------------------|
| `MAX_IMAGE_BYTES`    | 20 MB   | Reject larger uploads / fetches                     |
| `FETCH_TIMEOUT`      | 15 s    | Timeout when fetching `image_url`                   |
| `ALLOW_PRIVATE_URLS` | off     | `1` to allow fetching localhost / private IPs       |
| `FONT_PATH`          | —       | Override the label font with a specific `.ttf`      |

## Security notes

- **SSRF guard:** `image_url` fetches reject private, loopback, and link-local
  addresses by default (toggle with `ALLOW_PRIVATE_URLS=1` for local testing).
- **Bomb guard:** a pixel cap and a byte-size cap reject decompression bombs and
  oversized uploads.
- The service has no auth. Put it behind an API gateway, a shared secret header,
  or your platform's auth before exposing it publicly.

## Files

| File          | What                                             |
|---------------|--------------------------------------------------|
| `annotate.py` | Pure Pillow drawing logic (no web framework)     |
| `main.py`     | FastAPI app: input handling, guards, responses   |
| `examples/`   | `demo.py` renders a sample annotated image        |
| `Dockerfile`  | Container build with a bundled font               |
