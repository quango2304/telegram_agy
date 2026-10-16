# Seeing images

When someone asks about a picture — or sends a sticker — the worker fetches it and
hands it to `agy`, which opens the file and answers from what is actually in the
image.

## Which image gets attached

Priority order, capped at `MEDIA_MAX_PER_RUN` (default **5**):

1. a photo on the **trigger message itself** ("@bot cái này là gì" + photo),
2. a photo on the message the trigger **replies to** — the common flow: someone
   posts a photo, chatter follows, then someone replies to *that* photo,
3. otherwise any photo among the last `MEDIA_CONTEXT_MESSAGES` (default **10**)
   messages before the trigger — plus anything that landed after it — newest
   first. Stickers are skipped here.

Step 2 is why `messages.reply_to_tg_message_id` is stored. Without it, "what is
this?" replied onto an older photo would attach whatever image happened to be most
recent — and the bot would confidently describe the wrong picture.

Each attachment is labelled in the prompt with who sent it and its caption, so
several images in one run stay distinguishable.

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
photo downloads it (up to `MEDIA_MAX_PER_RUN`), even for "2 + 2". Lower
`MEDIA_CONTEXT_MESSAGES` if that bites.

**Why stickers are skipped in tier 3.** People spam them as punctuation, so a
proximity rule would burn the whole budget on decoration. A sticker is only
fetched when it *is* the trigger, or the trigger replies to it.

A scheduled run has no trigger message, so tier 3 never applies to it.

In a forum topic every non-reply message carries `reply_to_tg_message_id` = the
topic's root id (405 for the gold/VN-Index topic). Tier 2 looks that up, finds
no stored row, and moves on — it is not a real reply.

## What is and isn't fetched

- **Photos** and documents with an `image/*` mime.
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

## Fetching is lazy

Nothing is downloaded at ingest time. Only the Telegram `file_id` is stored
(`media_kind`, `media_file_id`, `media_mime`, `media_file_name` on `messages`), and
the file is fetched at reply time. A group full of memes costs nothing until
someone actually asks about one. `file_id` stays valid indefinitely, so an old
photo can still be fetched as long as its row is within retention.

## Where the file goes

The image is copied into **that run's temp workdir** — the same directory `agy`
runs in — chowned to the unprivileged `agy` user, and deleted with the workdir when
the run ends. Nothing lands in a shared volume, and the prompt refers to it by a
plain relative path (`./anh_<tg_message_id>.jpg`, or `./sticker_<tg_message_id>.webp`).

A failed download is skipped, never fatal: the run still answers, just without that
image.

The extension is a guess from the stored mime, then **corrected from the file's
magic bytes** after download (`_sniff_ext`). Telegram documents a sticker thumbnail
as ".WEBP or .JPG" without saying which, and a document's declared mime can lie —
`agy` picks its decoder by extension, so a JPEG named `.webp` simply fails to open.

## Cost

A reply that reads an image costs noticeably more and takes longer, because the
image goes through `agy`'s own file-read path. Measured on
`gemini-3.8-flash-medium`: **~15s** for a text reply vs **~23s** with one photo
attached.

`MEDIA_MAX_PER_RUN=0` turns image reading off entirely.
