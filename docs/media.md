# Seeing images

When someone asks about a picture, the worker fetches it and hands it to `agy`,
which opens the file and answers from what is actually in the image.

## Which image gets attached

Priority order, capped at `MEDIA_MAX_PER_RUN` (default **5**):

1. a photo on the **trigger message itself** ("@bot cái này là gì" + photo),
2. a photo on the message the trigger **replies to** — the common flow: someone
   posts a photo, chatter follows, then someone replies to *that* photo,
3. otherwise a photo posted **right around the trigger** — within
   `MEDIA_CONTEXT_SECONDS` (default 60s) of it.

Step 2 is why `messages.reply_to_tg_message_id` is stored. Without it, "what is
this?" replied onto an older photo would attach whatever image happened to be most
recent — and the bot would confidently describe the wrong picture.

Each attachment is labelled in the prompt with who sent it and its caption, so
several images in one run stay distinguishable.

**Why tier 3 is time-boxed.** Telegram sends an album as one captioned message
plus N bare image messages, all at the same instant — the mention lands on only
one of them, so the rest can only be found by proximity. But an untimed "newest
photos in the window" rule fires on *every* reply: in a photo-heavy group, asking
"2 + 2" would download the last `MEDIA_MAX_PER_RUN` images. The time window keeps
albums and "photo, then immediately asks" while costing nothing on unrelated
messages.

A scheduled run has no trigger message, so tier 3 never applies to it.

## What is and isn't fetched

- **Photos** and documents with an `image/*` mime. Nothing else.
- **Voice notes and audio are not transcribed.** Tested, not assumed: `agy` timed
  out on ogg/opus, and its mp3 attempt came back blocked by Gemini's filters.
  Audio is still stored and still shows as `[voice]` in the history — the bot just
  cannot hear it.
- Stickers, video, animations and video notes are recorded by kind so the history
  line stays honest, but are never downloaded.
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
plain relative path (`./anh_<tg_message_id>.jpg`).

A failed download is skipped, never fatal: the run still answers, just without that
image.

## Cost

A reply that reads an image costs noticeably more and takes longer, because the
image goes through `agy`'s own file-read path. Measured on
`gemini-3.8-flash-medium`: **~15s** for a text reply vs **~23s** with one photo
attached.

`MEDIA_MAX_PER_RUN=0` turns image reading off entirely.
