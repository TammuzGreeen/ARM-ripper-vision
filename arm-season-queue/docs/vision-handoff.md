# Qwen image handoff: first iteration

Webcam captures and Add photo can use an image-capable Qwen server. No reference
disc photograph, masterlist, episode database or subtitle transcript is sent to
the model. This is visible-information extraction, not reference-image matching.

The interface shows series, season, disc number, edition, printed episode ranges
and printed episode names. Each reading includes the model's quoted visible text
and image number. Missing information stays null/unknown. A printed episode range
does not imply a disc number or supply episode names that are not printed.
Quotes are model claims, not independently verified OCR. JSON validation cannot
prove factual correctness; inspect the retained images. Results never edit the
masterlist, pair with ARM insertions or authorize ripping in this first iteration.

## llama.cpp / TrueNAS configuration

Keep existing storage, camera, user/password and port settings. Add these environment
variables to the queue container, replacing the example address and model ID:

```yaml
CAMERA_MODE: manual
RECOGNITION_BACKEND: llamacpp
VISION_BASE_URL: "http://YOUR_AI_SERVER:8080/v1"
VISION_MODEL: "YOUR_EXACT_SERVED_MODEL_ID"
VISION_API_KEY: ""
VISION_TIMEOUT_SECONDS: "180"
```

The host and port above are placeholders, not a deployment assumption. Use the
model ID reported by the server's `GET /v1/models`; it may be an alias or a GGUF
filename rather than the model's marketing name. Leave the key empty only if your
server requires none. Keep actual keys, private addresses and model deployment
details out of Git. The container must resolve/reach the configured server.

llama.cpp must load a compatible vision encoder/projector and support image input.
A text-only working chat does not prove this. This adapter sends JPEG data URLs
to `/v1/chat/completions`, requests schema-constrained JSON and disables thinking
using `chat_template_kwargs.enable_thinking=false`. It does not install models,
change server settings or enable tools. See [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

If the UI reports that image input is unsupported, configure llama.cpp with the
matching model vision projector (`mmproj`) and restart that server. Changing only
the queue's model name cannot turn a text-only server into a vision server.

After updating the image/environment, refresh the UI. It should say that images
are sent to your configured vision server. Mark the empty view, place a disc,
capture, then inspect the retained image and visible-information fields. Add photo
uses the same backend for one uploaded image. Automatic camera mode sends its
three captured frames together, but vision results remain review-only.

## Other configurations

| Setting | Meaning |
| --- | --- |
| `RECOGNITION_BACKEND=tesseract` | Default; OCR stays inside the queue container; no image handoff. |
| `RECOGNITION_BACKEND=llamacpp` | Recommended for llama.cpp, including JSON schema and thinking control. |
| `RECOGNITION_BACKEND=openai-compatible` | Generic image chat-completions server; JSON-object mode plus local schema validation. Base includes `/v1`. |
| `RECOGNITION_BACKEND=ollama` | Native `/api/chat`, image list and schema response. Base is the origin, e.g. `http://YOUR_AI_SERVER:11434`, without `/api` or `/v1`. |
| `VISION_MODEL` | Required exact served image-capable model ID when using a vision backend. |
| `VISION_API_KEY` | Optional bearer token; never returned through the UI. |
| `VISION_TIMEOUT_SECONDS` | Per-network-operation timeout, default 180; allowed 10–600. |

For Ollama, see its [image/structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs).
No automatic protocol fallback is attempted; a wrong endpoint is reported, not
silently retried against another server. TLS certificates must validate normally.

## Data and error handling

Enabling a vision backend opts into transmitting captured/uploaded images to that
configured endpoint. It can be on your LAN; the application makes no cloud choice.
Only selected frames are transmitted, never the live stream. Images are limited to
three per request, resized to at most 2560 pixels on the longest side and JPEG
encoded. Full captured evidence remains in the authenticated local evidence store.
Images from different capture events are never combined in one request.

Timeouts, HTTP failures, incomplete output, invalid field types, impossible ranges,
extra fields and invalid image references are rejected. Retained images survive
recognition failures so a retry can be compared. Error messages omit response bodies,
server addresses and credentials. Redirects and ambient HTTP proxies are disabled.
There is no automatic retry/upload after failure; capture again when ready.

The automated tests verify request structure and error handling using synthetic
images and simulated server responses. They do not establish recognition accuracy
or compatibility with every deployed llama.cpp/model build. Test your actual server
with rotated discs, small lettering, empty scenes and unknown/unreadable fields.

## Planned sources and identification strategy

`app/menu_capture.py` defines a menu-image/provenance contract. Its capture function
explicitly raises NotImplementedError. No menu rendering, navigation, optical-drive
access or button tracing is implemented. The vision adapter has a menu source label
reserved for that integration; it is not exposed as an automatic capture feature.

`app/subtitle_analysis.py` is an explicit unsupported stub. No subtitle extraction,
OCR, lookup or episode analysis runs. Subtitles are deferred.

`app/disc_identity.py` reserves a versioned fingerprint/lookup boundary. No local
fingerprint cache or TheDiscDB/OVID requests run yet. Intended future sequence:

1. Compute a provider-compatible structural fingerprint, preserving its algorithm ID.
2. Check a local verified mapping, then configured disc databases for candidates.
3. Validate release and source-title/segment mappings against the actual scan.
4. On an unknown disc, inspect menu images and trace button destinations separately.
5. Compare extracted names with configured metadata sources and your masterlist.
6. Save a reviewed fingerprint-to-release/title mapping with provenance.

Database matches are candidates, not proof. Provider fingerprint schemes are not
interchangeable, and scan title numbers need not be stable. A menu's visual order
does not establish its navigation destination or the order of ripped files. No
reference-photo registration is required by this design.

[Disk-Rip's documentation](https://github.com/Schentrup-Software/Disk-Rip/blob/main/HOW-IT-WORKS.md)
describes TheDiscDB ContentHash lookup and joins using source files/segment maps;
its results remain advisory. [OVID](https://github.com/The-Artificer-of-Ciphers-LLC/OVID)
documents DVD fingerprinting while describing itself as pre-alpha. These are design
references, not integrated dependencies or coverage guarantees. Implementations
will require separate API, fingerprint and title-mapping compatibility checks.
