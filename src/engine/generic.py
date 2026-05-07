#!/usr/bin/env python3
# coding: utf-8

# ytdlbot - generic.py

import logging
import os
from pathlib import Path

import yt_dlp

from config import AUDIO_FORMAT
from utils import is_youtube
from database.model import get_format_settings, get_quality_settings
from engine.base import BaseDownloader


def match_filter(info_dict):
    if info_dict.get("is_live"):
        raise NotImplementedError("Skipping live video")
    return None  # Allow download for non-live videos


class YoutubeDownload(BaseDownloader):
    @staticmethod
    def get_format(m):
        return [
            f"bestvideo[ext=mp4][height={m}]+bestaudio[ext=m4a]",
            f"bestvideo[vcodec^=avc][height={m}]+bestaudio[acodec^=mp4a]/best[vcodec^=avc]/best",
            f"best[height<={m}]/bestvideo[height<={m}]+bestaudio/best",
        ]

    def _setup_formats(self) -> list | None:
        if not is_youtube(self._url):
            return [None]

        quality, format_ = get_quality_settings(self._chat_id), get_format_settings(self._chat_id)
        # quality: high, medium, low, custom
        # format: audio, video, document
        formats = []
        defaults = [
            # webm , vp9 and av01 are not streamable on telegram, so prefer mp4 first
            "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]/best[vcodec^=avc]/best",
            "18/best[height<=720]/best",
            None,
        ]
        audio = AUDIO_FORMAT or "m4a"
        maps = {
            "high-audio": [f"bestaudio[ext={audio}]/bestaudio/best"],
            "high-video": defaults,
            "high-document": defaults,
            "medium-audio": [f"bestaudio[ext={audio}]/bestaudio/best"],
            "medium-video": self.get_format(720),
            "medium-document": self.get_format(720),
            "low-audio": [f"bestaudio[ext={audio}]/bestaudio/best"],
            "low-video": self.get_format(480),
            "low-document": self.get_format(480),
            "custom-audio": "",
            "custom-video": "",
            "custom-document": "",
        }

        if quality == "custom":
            pass
            # TODO not supported yet

        formats.extend(maps[f"{quality}-{format_}"])
        # extend default formats if not high*
        if quality != "high":
            formats.extend(defaults)
        return formats

    def _download(self, formats) -> list:
        output = Path(self._tempdir.name, "%(title).70s.%(ext)s").as_posix()
        ydl_opts = {
            "progress_hooks": [lambda d: self.download_hook(d)],
            "outtmpl": output,
            "restrictfilenames": False,
            "quiet": True,
            "match_filter": match_filter,
            "concurrent_fragments": 16,
            "buffersize": 4194304,
            "retries": 6,
            "fragment_retries": 6,
            "skip_unavailable_fragments": True,
            "embed_metadata": True,
            "embed_thumbnail": True,
            "writethumbnail": False,
        }
        # setup cookies and JS challenge support for youtube only
        if is_youtube(self._url):
            # use cookies from browser firstly
            if browsers := os.getenv("BROWSERS"):
                ydl_opts["cookiesfrombrowser"] = browsers.split(",")
            if os.path.isfile("youtube-cookies.txt") and os.path.getsize("youtube-cookies.txt") > 100:
                ydl_opts["cookiefile"] = "youtube-cookies.txt"

            deno_path = os.getenv("YOUTUBE_DENO_PATH", "/root/.deno/bin/deno")
            if deno_path and os.path.isfile(deno_path):
                ydl_opts["js_runtimes"] = {"deno": {"path": deno_path}}
                ydl_opts["remote_components"] = {"ejs:github"}

            ydl_opts["extractor_args"] = {"youtube": {"player_client": ["web"]}}

            # try add po token if present
            if potoken := os.getenv("POTOKEN"):
                ydl_opts["extractor_args"]["youtube"]["po_token"] = [f"web+{potoken}"]

        if self._url.startswith("https://drive.google.com"):
            # Always use the `source` format for Google Drive URLs.
            formats = ["source"] + formats

        files = None
        last_error = None
        for f in formats:
            ydl_opts["format"] = f
            logging.info("yt-dlp options: %s", ydl_opts)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([self._url])
                files = list(Path(self._tempdir.name).glob("*"))
                if files:
                    break
            except yt_dlp.utils.DownloadError as e:
                last_error = e
                logging.warning("Format %s failed for %s: %s", f, self._url, e)
                continue

        if not files and last_error:
            raise last_error
        return files

    def _start(self, formats=None):
        # start download and upload, no cache hit
        # user can choose format by clicking on the button(custom config)
        default_formats = self._setup_formats()
        if formats is not None:
            # formats according to user choice
            default_formats = formats + self._setup_formats()
        self._download(default_formats)
        self._upload()
