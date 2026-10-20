# Images and files

When someone asks about a picture — or sends a sticker — the worker fetches it and
hands it to `agy`, which opens the file and answers from what is actually in the
image. Files sent in the chat (epub, pdf, zip…) are fetched the same way, so
"@bot up file này lên drive" has a real file to upload — see
[Files](#files-epub-pdf-zip).

## Which image or file gets attached

Priority order, capped at `MEDIA_MAX_PER_RUN` (default **5**):

1. a photo on the **trigger message itself** ("@bot cái này là gì" + photo),
2. a photo on the message the trigger **replies to** — the common flow: someone
   posts a photo, chatter follows, then someone replies to *that* photo,
3. otherwise any photo or sticker among the last `MEDIA_CONTEXT_MESSAGES`
   (default **10**) messages before the trigger — plus anything that landed
   after it — newest first.

Step 2 is why `messages.reply_to_tg_message_id` is stored. Without it, "what is
this?" replied onto an older photo would attach whatever image happened to be most
recent — and the bot would confidently describe the wrong picture.

Each attachment is labelled in the prompt with who sent it and its caption, so
several images in one run stay distinguishable.

Files go through the same three tiers and share the same `MEDIA_MAX_PER_RUN`
budget, so a file posted within the last 10 messages is attached even when the
mention isn't a reply to it.

**Why tier 3 counts messages, not seconds.** It used to be a 60-second window
around the trigger. A slow group does "photo → *đỉnh đỉnh* → three minutes
later, @bot cái gì tái sinh z" — an @-mention, not a reply. On prod
(2026-10-15, topic 405) that got `media=0` twice in a row: the bot answered from
the bare `[ảnh]` history line, said it couldn't see anything, and the follow-up
"ủa chưa đọc dc hình ảnh hả" missed the photo for the same reason. Counting
messages fits how a conversation actually moves. It still covers albums:
Telegram sends one as a captioned message plus N bare image messages at the same
instant, the mention lands on one of them, and the rest sit right beside it —
some sorted *after* the trigger, which is why those are included too.

The trade-off is cost: in a photo-heavy group, any reply within 10 messages of a
photo or sticker downloads it (up to `MEDIA_MAX_PER_RUN`), even for "2 + 2". The
same goes for files: in a book-sharing group a mention near a few books downloads
each of them (up to 20 MB apiece). That costs download time, not tokens — `agy`
is told not to open a file unless someone asks about its contents. Lower
`MEDIA_CONTEXT_MESSAGES` if that bites.

**Stickers count like photos in tier 3** (since 2026-10-16). They used to be
skipped there as "punctuation", but then "@bot sticker này nghĩa là gì" a couple
of messages after the sticker got nothing. In a sticker-heavy chat they can now
take part of the `MEDIA_MAX_PER_RUN` budget; newest first, so the closest ones
win.

A scheduled run has no trigger message, so tier 3 never applies to it.

In a forum topic every non-reply message carries `reply_to_tg_message_id` = the
topic's root id (405 for the gold/VN-Index topic). Tier 2 looks that up, finds
no stored row, and moves on — it is not a real reply.

## What is and isn't fetched

- **Photos** and documents with an `image/*` mime — shown to `agy` as pictures.
- **Any other document** (since 2026-10-20) — handed over as a file; see
  [Files](#files-epub-pdf-zip).
- **Stickers**, since they are how half a Vietnamese group chat actually talks. A
  static sticker is a `.webp` and goes to `agy` as-is. An animated (`.tgs`,
  gzipped Lottie) or video (`.webm`) sticker cannot be opened, so what is stored
  is its **thumbnail's** `file_id` — a still frame, which is enough to get the
  joke. `media_file_id` is only ever read to *show* `agy` a picture, never to
  re-send the file, so the swap needs no extra column. A sticker with no
  thumbnail at all is stored without a mime and never fetched.
- **Voice notes and audio are not transcribed.** Tested, not assumed: `agy` timed
  out on ogg/opus, and its mp3 attempt came back blocked by Gemini's filters.
  Audio is still stored and still shows as `[voice]` in the history — the bot just
  cannot hear it.
- Video, animations and video notes are recorded by kind so the history line stays
  honest, but are never downloaded.
- Telegram's `getFile` caps downloads at 20 MB (`MEDIA_MAX_DOWNLOAD_MB`).

## Files (epub, pdf, zip…)

Before 2026-10-20 only images were fetched. On prod, "@bot tải file này up lên
drive giùm" replied onto an epub got `media=0`: `agy` only saw the history line
`[file: AI_Engineering….epub]`, then spent 436s (of a 480s budget) hunting
`/tmp`, Drive, the ebook vault and the web for a book that was never handed
over, before admitting it couldn't. Now the document is downloaded into the
workdir and listed in a separate **FILE ĐÍNH KÈM** prompt block with its path,
its original name and who sent it. The block tells `agy` to:

- use *that* file for anything asked of it (upload to Drive, forward, convert)
  instead of searching elsewhere, and keep the original name when uploading —
  the workdir copy has a generated one;
- not read a binary epub/pdf/zip unless someone asks about its contents — the
  image block's "open it and look" would cost a fortune on a 20 MB book.

**The workdir copy is always named `file_<tg_message_id>.<ext>`**, never the
sender's filename. `agy` loads `AGENTS.md`/`GEMINI.md` from its cwd as hard
workspace rules, so a group member sending a file called that would otherwise be
writing the bot's rules. Only the extension is kept (from the filename, else the
mime, else `.bin`). See [security.md](security.md).

**Over 20 MB it cannot be fetched** — Bot API `getFile` refuses it, and lifting
that needs a self-hosted Bot API server. Rather than drop it silently like an
image, the file is still listed, marked *KHÔNG tải được*, so `agy` says so
straight away instead of repeating the 436s hunt.

## Fetching is lazy

Nothing is downloaded at ingest time. Only the Telegram `file_id` is stored
(`media_kind`, `media_file_id`, `media_mime`, `media_file_name` on `messages`), and
the file is fetched at reply time. A group full of memes costs nothing until
someone actually asks about one. `file_id` stays valid indefinitely, so an old
photo can still be fetched as long as its row is within retention.

## Where the file goes

The image (or file) is copied into **that run's temp workdir** — the same directory `agy`
runs in — chowned to the unprivileged `agy` user, and deleted with the workdir when
the run ends. Nothing lands in a shared volume, and the prompt refers to it by a
plain relative path (`./anh_<tg_message_id>.jpg`, `./sticker_<tg_message_id>.webp`,
or `./file_<tg_message_id>.epub`).

A failed download is skipped, never fatal: the run still answers, just without that
image.

For an image, the extension is a guess from the stored mime, then **corrected
from the file's magic bytes** after download (`_sniff_ext`). Telegram documents a sticker thumbnail
as ".WEBP or .JPG" without saying which, and a document's declared mime can lie —
`agy` picks its decoder by extension, so a JPEG named `.webp` simply fails to open.

## Cost

A reply that reads an image costs noticeably more and takes longer, because the
image goes through `agy`'s own file-read path. Measured on
`gemini-3.8-flash-medium`: **~15s** for a text reply vs **~23s** with one photo
attached.

`MEDIA_MAX_PER_RUN=0` turns image reading — and file fetching — off entirely.
