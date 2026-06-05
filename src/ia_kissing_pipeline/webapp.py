from __future__ import annotations

import base64
import io
import json
import math
import os
import signal
import sqlite3
import shutil
import subprocess
import sys
import time
import re
import zipfile
from pathlib import Path
from urllib import request as urllib_request

from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, url_for
from PIL import Image, ImageDraw

from ia_kissing_pipeline.config import load_settings
from ia_kissing_pipeline.db import get_connection, init_db
from ia_kissing_pipeline.main import _resolve_source_video
from ia_kissing_pipeline.ingest.ia_client import IAClient
from ia_kissing_pipeline.ingest.ia_ingest import ingest_from_ia, make_checkpoint_key
from ia_kissing_pipeline.main import run_metadata_scoring
from ia_kissing_pipeline.utils.time import utc_now_iso


READY_TARGET = 20
QUEUE_INGEST_QUERY = "collection:feature_films"
QUEUE_INGEST_LIMIT = 8
QUEUE_INGEST_ROWS = 4
QUEUE_STALE_SECONDS = 600
QUEUE_NAME = "download_batch"
VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".webm"}


EMPTY_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>IA Kissing Review</title>
  <style>
    :root { color-scheme: dark; --bg: #0d1117; --panel: #151b23; --panel-2: #0f141b; --border: #263244; --text: #edf3ff; --muted: #94a4bd; --link: #7cc7ff; }
    body { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; margin: 24px; background: radial-gradient(circle at top, #182334 0%, var(--bg) 55%); color: var(--text); }
    a { color: var(--link); text-decoration: none; }
    .panel { max-width: 860px; background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%); border: 1px solid var(--border); border-radius: 16px; padding: 18px; box-shadow: 0 18px 60px rgba(0,0,0,0.32); }
    .mono { font-family: monospace; font-size: 13px; }
  </style>
</head>
<body>
  <p><a href="{{ url_for('films_index') }}">Database</a> | <a href="{{ url_for('review_data_index') }}">Review Data</a> | <a href="{{ url_for('admin_index') }}">Admin</a> | <a href="{{ url_for('clips_index') }}">Clips</a></p>
  <div class="panel">
    <h1>No Ready Film Yet</h1>
    <p>The review queue is being filled in the background.</p>
    <p class="mono">ready={{ ready_count }} target={{ target_ready }} queue_job={{ queue_status }}</p>
  </div>
</body>
</html>
"""


FILMS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>IA Kissing Review</title>
  <style>
    :root { color-scheme: dark; --bg: #0d1117; --panel: #151b23; --panel-2: #111720; --border: #263244; --text: #edf3ff; --muted: #93a4bc; --link: #7cc7ff; }
    body { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; margin: 24px; background: radial-gradient(circle at top, #182334 0%, var(--bg) 55%); color: var(--text); }
    a { color: var(--link); text-decoration: none; }
    table { border-collapse: collapse; width: 100%; background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%); border: 1px solid var(--border); border-radius: 18px; overflow: hidden; }
    th, td { border-bottom: 1px solid var(--border); padding: 10px; text-align: left; vertical-align: top; }
    th { background: #1b2430; color: #c8d5e6; }
    .status { font-family: monospace; font-size: 13px; }
    .status-badge { display: inline-block; padding: 4px 9px; border-radius: 999px; border: 1px solid #324054; background: #1b2430; color: #c8d5e6; }
    .status-badge.downloading { background: #103923; border-color: #26714a; color: #87f0ae; font-weight: 700; }
    .status-badge.pending { background: #132c49; border-color: #2b6aa7; color: #91ccff; }
    .status-badge.awaiting_download { background: #3b2c11; border-color: #88611b; color: #ffcf75; }
    .status-badge.checking_metadata, .status-badge.checking_title { background: #2d1f45; border-color: #67489d; color: #ccb0ff; }
    .status-badge.source_error, .status-badge.excluded_metadata, .status-badge.excluded_manual, .status-badge.reviewed_no_kiss, .status-badge.reviewed_has_kiss { background: #252d37; border-color: #3e4d61; color: #94a4bd; }
    .stale td { color: #66758a; background: #10161d; }
    .icon-button { padding: 6px 10px; line-height: 1; background: #2a3442; color: #d9e5f7; border: 1px solid #3b495d; border-radius: 10px; }
    .action-link { display: inline-block; padding: 6px 10px; line-height: 1; background: #2a3442; color: #d9e5f7; border: 1px solid #3b495d; border-radius: 10px; text-decoration: none; }
    .action-link:hover { filter: brightness(1.08); }
    .film-tag-button { transition: transform 120ms ease, box-shadow 120ms ease, filter 120ms ease; cursor: pointer; }
    .film-tag-button:hover { transform: translateY(-1px); filter: brightness(1.08); box-shadow: 0 8px 20px rgba(0,0,0,0.24); }
    .film-tag-button[data-tag="kiss"] { background: linear-gradient(180deg, #6e2040 0%, #4e1530 100%); border-color: #a54c73; color: #ffd0e2; }
    .film-tag-button[data-tag="phone"] { background: linear-gradient(180deg, #16395f 0%, #102845 100%); border-color: #3f79b5; color: #c3e4ff; }
    .film-tag-button[data-tag="cry"] { background: linear-gradient(180deg, #224f43 0%, #16382f 100%); border-color: #4ca186; color: #c8ffea; }
    .film-tag-button[data-tag="dance"] { background: linear-gradient(180deg, #5d3f12 0%, #412b0c 100%); border-color: #b8872f; color: #ffe2a3; }
    .film-tag-button.muted-tag { filter: grayscale(0.9) brightness(0.72); opacity: 0.72; }
    .filter-button { text-decoration: none; }
    .filter-button.active { outline: 2px solid #edf3ff; box-shadow: 0 0 0 3px rgba(255,255,255,0.14); }
    .clear-filter { display: inline-block; padding: 6px 10px; line-height: 1; background: #2a3442; color: #d9e5f7; border: 1px solid #3b495d; border-radius: 10px; text-decoration: none; }
    .mode-toggle { display: inline-flex; gap: 6px; align-items: center; margin-left: auto; }
    .mode-toggle form { margin: 0; }
    .mode-toggle button { padding: 6px 10px; line-height: 1; background: #2a3442; color: #d9e5f7; border: 1px solid #3b495d; border-radius: 10px; cursor: pointer; }
    .mode-toggle button.active { background: #132c49; border-color: #2b6aa7; color: #91ccff; }
    #tag-picker .film-tag-button[data-tag="kiss"] { background: linear-gradient(180deg, #6e2040 0%, #4e1530 100%); border-color: #a54c73; color: #ffd0e2; }
    #tag-picker .film-tag-button[data-tag="phone"] { background: linear-gradient(180deg, #16395f 0%, #102845 100%); border-color: #3f79b5; color: #c3e4ff; }
    #tag-picker .film-tag-button[data-tag="cry"] { background: linear-gradient(180deg, #224f43 0%, #16382f 100%); border-color: #4ca186; color: #c8ffea; }
    #tag-picker .film-tag-button[data-tag="dance"] { background: linear-gradient(180deg, #5d3f12 0%, #412b0c 100%); border-color: #b8872f; color: #ffe2a3; }
    .clips-row td { background: #0f141b; padding: 16px; }
    .clips-drawer { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .clip-tile { background: #0b1016; border: 1px solid #263244; border-radius: 12px; overflow: hidden; }
    .clips-drawer video { width: 100%; background: #000; border-radius: 12px 12px 0 0; display: block; }
    .clip-actions { display: flex; gap: 8px; padding: 8px; justify-content: flex-end; }
    .clip-actions button { padding: 7px 10px; line-height: 1; background: #2a3442; color: #d9e5f7; border: 1px solid #3b495d; border-radius: 10px; cursor: pointer; font-size: 13px; }
    .clip-actions .ignore-button { background: #3b2c11; border-color: #88611b; color: #ffcf75; }
    .clip-actions .delete-button { background: #3a2025; border-color: #8d4b58; color: #ffd3db; }
    .clips-empty { color: var(--muted); font-size: 13px; }
    tbody tr[data-film-id] { cursor: pointer; }
    tbody tr[data-film-id]:hover td { background: #17202b; }
    @media (max-width: 900px) {
      body { margin: 14px; }
      th, td { padding: 8px; }
      .clips-drawer { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .icon-button, .film-tag-button { min-height: 40px; }
    }
  </style>
</head>
<body>
  <p><a href="{{ url_for('index') }}">Next Review</a> | <a href="{{ url_for('review_data_index') }}">Review Data</a> | <a href="{{ url_for('admin_index') }}">Admin</a> | <a href="{{ url_for('clips_index') }}">Clips</a></p>
  <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
    <h1 style="margin:0;">Film Database</h1>
    {% for stat in tag_stats %}
      <span class="status-badge pending film-tag-button" data-tag="{{ stat['tag'] }}">{{ stat["tag"] }} total: {{ stat["count"] }}</span>
    {% endfor %}
    {% if active_filter_tag %}
      <span class="small" style="color: var(--muted);">filtered by {{ active_filter_tag }}</span>
      <a class="clear-filter" href="{{ url_for('films_index') }}">Remove Filter</a>
    {% endif %}
    <div class="mode-toggle">
      <span class="small" style="color: var(--muted);">Clip API</span>
      <form method="post" action="{{ url_for('set_clip_order_mode') }}">
        <input type="hidden" name="mode" value="random">
        <button type="submit" class="{{ 'active' if clip_order_mode == 'random' else '' }}">Random</button>
      </form>
      <form method="post" action="{{ url_for('set_clip_order_mode') }}">
        <input type="hidden" name="mode" value="ordered">
        <button type="submit" class="{{ 'active' if clip_order_mode == 'ordered' else '' }}">Ordered</button>
      </form>
    </div>
  </div>
  <div style="margin: 0 0 18px 0; padding: 14px; background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%); border: 1px solid var(--border); border-radius: 16px;">
    <div style="font-size: 13px; color: var(--muted); margin-bottom: 8px;">Review Tags</div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
      {% for tag in ["kiss", "phone", "cry", "dance"] %}
        <a class="status-badge pending film-tag-button filter-button {{ 'active' if active_filter_tag == tag else '' }}" href="{{ url_for('films_index', tag=tag) }}" data-filter-tag="{{ tag }}" data-tag="{{ tag }}">{{ tag }}</a>
      {% endfor %}
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Title</th>
        <th>Tags</th>
        <th>Pipeline</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody>
      {% for film in films %}
      <tr data-film-id="{{ film['id'] }}" class="{{ 'stale' if film['is_dimmed'] else '' }}">
        <td>{{ film["id"] }}</td>
        <td data-col="title">{{ film["title"] }}</td>
        <td data-col="tags">{{ film["tags_html"]|safe }}</td>
        <td class="status" data-col="pipeline"><span class="status-badge {{ film['pipeline_status'] }}">{{ film["pipeline_status"] }}</span></td>
        <td data-col="action">
          {% if film["show_open_link"] %}
            <a class="action-link" href="{{ url_for('film_detail', film_id=film['id']) }}">Open</a>
          {% endif %}
          <form method="post" action="{{ url_for('force_exclude_route', film_id=film['id']) }}" style="display:inline-block; margin-left:8px;">
            <button class="ghost icon-button" type="submit" title="Force exclude">&#128465;</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
<script>
  const activeFilterTag = {{ active_filter_tag|tojson }};
  const refreshFilms = async () => {
    const statusUrl = activeFilterTag ? `{{ url_for('films_status') }}?tag=${encodeURIComponent(activeFilterTag)}` : "{{ url_for('films_status') }}";
    const response = await fetch(statusUrl);
    const payload = await response.json();
    const tbody = document.querySelector("tbody");
    if (!tbody) return;
    const ordered = [...payload.films];
    const seenIds = new Set(ordered.map((film) => String(film.id)));
    ordered.forEach((film) => {
      let row = tbody.querySelector(`tr[data-film-id="${film.id}"]`);
      if (!row) {
        row = document.createElement("tr");
        row.dataset.filmId = film.id;
        row.innerHTML = `
          <td>${film.id}</td>
          <td data-col="title"></td>
          <td data-col="tags"></td>
          <td class="status" data-col="pipeline"></td>
          <td data-col="action"></td>
        `;
        tbody.appendChild(row);
      }
      row.className = film.is_dimmed ? "stale" : "";
      row.querySelector('[data-col="title"]').textContent = film.title;
      row.querySelector('[data-col="tags"]').innerHTML = film.tags_html;
      row.querySelector('[data-col="pipeline"]').innerHTML = `<span class="status-badge ${film.pipeline_status}">${film.pipeline_status}</span>`;
      row.querySelector('[data-col="action"]').innerHTML = `${film.show_open_link ? `<a class="action-link" href="/films/${film.id}">Open</a>` : ""}<form method="post" action="/films/${film.id}/force-exclude" style="display:inline-block; margin-left:8px;"><button class="ghost icon-button" type="submit" title="Force exclude">&#128465;</button></form>`;
    });
    tbody.querySelectorAll("tr[data-film-id]").forEach((row) => {
      if (!seenIds.has(String(row.dataset.filmId))) {
        const nextRow = row.nextElementSibling;
        if (nextRow && nextRow.classList.contains("clips-row") && nextRow.dataset.filmId === row.dataset.filmId) {
          nextRow.remove();
        }
        row.remove();
      }
    });
    bindTagButtons();
    bindFilmRows();
    window.setTimeout(refreshFilms, 3000);
  };
  const closeExistingDrawers = (exceptFilmId = null) => {
    document.querySelectorAll("tr.clips-row").forEach((row) => {
      if (exceptFilmId && row.dataset.filmId === String(exceptFilmId)) return;
      row.remove();
    });
  };
  const bindTagButtons = () => {
    document.querySelectorAll(".film-tag-button").forEach((button) => {
      if (button.dataset.bound === "1") return;
      button.dataset.bound = "1";
      button.addEventListener("click", async () => {
        if (!button.dataset.filmId) return;
        const filmId = button.dataset.filmId;
        const tag = button.dataset.tag;
        const hostRow = button.closest("tr[data-film-id]");
        const nextRow = hostRow?.nextElementSibling;
        if (nextRow && nextRow.classList.contains("clips-row") && nextRow.dataset.filmId === filmId) {
          nextRow.remove();
          return;
        }
        closeExistingDrawers(filmId);
        const response = await fetch(`/films/${filmId}/clips?tag=${encodeURIComponent(tag)}`);
        const payload = await response.json();
        const drawerRow = document.createElement("tr");
        drawerRow.className = "clips-row";
        drawerRow.dataset.filmId = filmId;
        const drawerCell = document.createElement("td");
        drawerCell.colSpan = 5;
        if (!payload.clips.length) {
          drawerCell.innerHTML = `<div class="clips-empty">No clips for tag "${tag}" yet.</div>`;
        } else {
          drawerCell.innerHTML = `<div class="clips-drawer">${payload.clips.map((clip) => `
            <div class="clip-tile" data-clip-id="${clip.id}">
              <video preload="metadata" controls src="/media/${clip.kind}/${clip.relpath}"></video>
              <div class="clip-actions">
                <button type="button" class="js-delete-db-clip delete-button" data-clip-id="${clip.id}" title="Delete clip">🗑 Delete</button>
                <button type="button" class="js-ignore-db-clip ignore-button" data-clip-id="${clip.id}" title="Ignore clip">🚫 Ignore</button>
              </div>
            </div>
          `).join("")}</div>`;
        }
        drawerRow.appendChild(drawerCell);
        hostRow.insertAdjacentElement("afterend", drawerRow);
        bindDrawerClipButtons();
      });
    });
  };
  const postForm = async (url, data) => {
    const body = new URLSearchParams(data);
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body,
    });
    return response;
  };
  const bindDrawerClipButtons = () => {
    document.querySelectorAll(".js-delete-db-clip").forEach((button) => {
      if (button.dataset.bound === "1") return;
      button.dataset.bound = "1";
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const clipId = button.dataset.clipId;
        const tile = button.closest(".clip-tile");
        const response = await postForm(`/clips/${clipId}/delete`, {});
        if (response.ok && tile) {
          tile.remove();
        }
      });
    });
    document.querySelectorAll(".js-ignore-db-clip").forEach((button) => {
      if (button.dataset.bound === "1") return;
      button.dataset.bound = "1";
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const clipId = button.dataset.clipId;
        const tile = button.closest(".clip-tile");
        const response = await postForm(`/clips/${clipId}/ignore`, {});
        if (response.ok && tile) {
          tile.remove();
        }
      });
    });
  };
  const bindFilmRows = () => {
    document.querySelectorAll('tbody tr[data-film-id]').forEach((row) => {
      if (row.dataset.rowBound === "1") return;
      row.dataset.rowBound = "1";
      row.addEventListener("click", (event) => {
        if (event.target.closest('button, a, form, video')) return;
        const openLink = row.querySelector('a.action-link[href^="/films/"]');
        if (openLink && !row.classList.contains("stale")) {
          window.location = openLink.href;
        }
      });
    });
  };
  bindTagButtons();
  bindDrawerClipButtons();
  bindFilmRows();
  window.setTimeout(refreshFilms, 3000);
</script>
</body>
</html>
"""


ADMIN_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>IA Kissing Admin</title>
  <style>
    :root { color-scheme: dark; --bg: #0d1117; --panel: #151b23; --panel-2: #0f141b; --border: #263244; --text: #edf3ff; --muted: #94a4bd; --link: #7cc7ff; }
    body { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; margin: 24px; background: radial-gradient(circle at top, #182334 0%, var(--bg) 55%); color: var(--text); }
    a { color: var(--link); text-decoration: none; }
    .panel { max-width: 860px; background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%); border: 1px solid var(--border); border-radius: 16px; padding: 18px; box-shadow: 0 18px 60px rgba(0,0,0,0.32); }
    .mono { font-family: monospace; font-size: 13px; }
    .muted { color: var(--muted); }
    form { display: flex; gap: 10px; align-items: end; flex-wrap: wrap; margin-top: 18px; }
    label { display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
    input[type="number"] { width: 110px; padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); background: #0d1117; color: var(--text); }
    button { padding: 9px 14px; border-radius: 10px; border: 1px solid #3b495d; background: #2a3442; color: #d9e5f7; cursor: pointer; }
    .status { margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--border); }
  </style>
</head>
<body>
  <p><a href="{{ url_for('index') }}">Next Review</a> | <a href="{{ url_for('films_index') }}">Database</a> | <a href="{{ url_for('review_data_index') }}">Review Data</a> | <a href="{{ url_for('clips_index') }}">Clips</a></p>
  <div class="panel">
    <h1 style="margin-top:0;">Admin</h1>
    <p class="muted">Launch the get more films batch from the web UI. This queues the same download-batch flow used by the Python tooling.</p>
    {% if message %}
      <p>{{ message }}</p>
    {% endif %}
    <form method="post" action="{{ url_for('admin_start_get_more_vids') }}">
      <label>
        Films to add
        <input type="number" name="count" min="1" max="100" value="{{ requested_count }}">
      </label>
      <button type="submit">Run Get More Films</button>
    </form>
    <div class="status">
      <h2 style="margin-top:0;">Queue Status</h2>
      <p class="mono">active_pool={{ ready_count }}</p>
      {% if queue_job %}
        <p class="mono">job_id={{ queue_job["id"] }} status={{ queue_job["status"] }} phase={{ queue_job["phase"] }} progress={{ queue_job["progress_percent"] }}%</p>
        <p>{{ queue_job["status_text"] }}</p>
      {% else %}
        <p class="mono">idle</p>
      {% endif %}
    </div>
  </div>
</body>
</html>
"""


FILM_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ film["title"] }}</title>
  <style>
    :root { color-scheme: dark; --bg: #0d1117; --panel: #151b23; --panel-2: #111720; --border: #263244; --text: #edf3ff; --muted: #94a4bd; --link: #7cc7ff; --accent: #2f81f7; --accent-2: #123563; }
    body { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; margin: 24px; background: radial-gradient(circle at top, #182334 0%, var(--bg) 55%); color: var(--text); }
    a { color: var(--link); text-decoration: none; }
    .topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 14px; }
    .panel { background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%); border: 1px solid var(--border); padding: 16px; margin-bottom: 20px; border-radius: 18px; box-shadow: 0 18px 60px rgba(0,0,0,0.28); }
    .panel h2 { margin-top: 0; }
    video { width: 100%; max-height: 74vh; background: black; display: block; }
    button, input { font: inherit; }
    button { padding: 8px 12px; background: linear-gradient(180deg, #2666b8 0%, var(--accent-2) 100%); color: white; border: 1px solid #3a78c4; border-radius: 12px; cursor: pointer; }
    .ghost { background: #2a3442; border-color: #3b495d; }
    .danger { background: #5e2534; border-color: #884055; }
    .meta { font-family: monospace; font-size: 13px; }
    .mark { border-top: 1px solid var(--border); padding: 10px 0; }
    .note { width: 100%; min-height: 70px; background: #0f141b; color: var(--text); border: 1px solid #334152; border-radius: 12px; padding: 10px; }
    input[type="number"] { background: #0f141b; color: var(--text); border: 1px solid #334152; border-radius: 10px; padding: 6px 8px; }
    .small { color: var(--muted); font-size: 14px; }
    .skim-shell { margin-top: 12px; }
    .skim-viewport { position: relative; background: #05080d; border: 1px solid #314056; border-radius: 18px; overflow: hidden; }
    .skim-overlay { position: absolute; top: 10px; left: 10px; right: 10px; display: flex; justify-content: space-between; pointer-events: none; color: #f7e9d7; font: 12px/1.2 monospace; text-shadow: 0 1px 2px rgba(0,0,0,0.7); }
    .skim-viewport video { cursor: ew-resize; touch-action: none; user-select: none; -webkit-user-select: none; }
    .skim-help { margin-top: 8px; color: var(--muted); font-size: 13px; }
    .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
    .metadata-row { display: grid; grid-template-columns: minmax(180px, 280px) minmax(0, 1fr); gap: 16px; align-items: start; }
    .metadata-title { font-size: clamp(24px, 4vw, 38px); line-height: 1.05; font-weight: 700; }
    .metadata-box { max-height: 240px; overflow: auto; background: #0b1016; border: 1px solid #314056; border-radius: 14px; padding: 4px 0; }
    .metadata-item { display: grid; grid-template-columns: minmax(130px, 180px) minmax(0, 1fr); gap: 14px; padding: 10px 14px; border-top: 1px solid rgba(49, 64, 86, 0.65); }
    .metadata-item:first-child { border-top: 0; }
    .metadata-label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    .metadata-value { font: 13px/1.45 monospace; color: #d7e5f7; white-space: pre-wrap; word-break: break-word; }
    .fold-section { border: 1px solid #314056; border-radius: 16px; background: #0b1016; overflow: hidden; }
    .fold-section summary { list-style: none; cursor: pointer; padding: 14px 16px; font-weight: 700; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .fold-section summary::-webkit-details-marker { display: none; }
    .fold-section summary::after { content: "+"; color: var(--muted); font-size: 22px; line-height: 1; }
    .fold-section[open] summary::after { content: "−"; }
    .fold-body { padding: 0 16px 16px; border-top: 1px solid rgba(49, 64, 86, 0.65); }
    .skim-overview-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-top: 14px; }
    .skim-frame-card { background: #0f141b; border: 1px solid #263244; border-radius: 12px; overflow: hidden; }
    .skim-frame-card img { width: 100%; display: block; background: #000; aspect-ratio: 16 / 9; object-fit: cover; }
    .skim-frame-meta { padding: 8px 10px; font: 12px/1.35 monospace; color: #c8d5e6; }
    .skim-frame-actions { display: flex; justify-content: flex-end; padding: 0 10px 10px; }
    .skim-frame-actions a { font-size: 11px; color: var(--muted); text-decoration: none; }
    .skim-frame-actions a:hover { color: var(--link); text-decoration: underline; }
    .skim-overview-status { margin-top: 14px; color: var(--muted); font-size: 13px; }
    .kiss-detector-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; margin-top: 14px; }
    .action-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
    .button-link { display: inline-flex; align-items: center; padding: 8px 12px; background: linear-gradient(180deg, #2666b8 0%, var(--accent-2) 100%); color: white; border: 1px solid #3a78c4; border-radius: 12px; cursor: pointer; text-decoration: none; }
    .button-link.ghost-link { background: #2a3442; border-color: #3b495d; }
    .button-link[aria-disabled="true"] { opacity: 0.55; pointer-events: none; }
    .debug-box { margin-top: 12px; padding: 12px; border-radius: 12px; border: 1px solid #314056; background: #0b1016; color: #d7e5f7; font: 12px/1.45 monospace; white-space: pre-wrap; word-break: break-word; }
    .debug-toggle { background: #2a3442; border-color: #3b495d; }
    body.debug-off .debug-only { display: none; }
    .film-tag-button { transition: transform 120ms ease, box-shadow 120ms ease, filter 120ms ease; cursor: pointer; }
    .film-tag-button:hover { transform: translateY(-1px); filter: brightness(1.08); box-shadow: 0 8px 20px rgba(0,0,0,0.24); }
    .tag-radio { position: absolute; opacity: 0; pointer-events: none; }
    .tag-option { display: inline-flex; }
    .tag-selector .film-tag-button[data-tag="kiss"] { background: linear-gradient(180deg, #6e2040 0%, #4e1530 100%); border-color: #a54c73; color: #ffd0e2; }
    .tag-selector .film-tag-button[data-tag="phone"] { background: linear-gradient(180deg, #16395f 0%, #102845 100%); border-color: #3f79b5; color: #c3e4ff; }
    .tag-selector .film-tag-button[data-tag="cry"] { background: linear-gradient(180deg, #224f43 0%, #16382f 100%); border-color: #4ca186; color: #c8ffea; }
    .tag-selector .film-tag-button[data-tag="dance"] { background: linear-gradient(180deg, #5d3f12 0%, #412b0c 100%); border-color: #b8872f; color: #ffe2a3; }
    .tag-selector .tag-radio:checked + .film-tag-button { opacity: 1; outline: 2px solid #87f0ae; }
    .tag-selector .tag-radio:not(:checked) + .film-tag-button { opacity: 0.65; }
    @media (max-width: 900px) {
      .metadata-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body class="debug-off">
  <div class="topbar">
    <p><a href="{{ url_for('index') }}">Next Review</a> | <a href="{{ url_for('films_index') }}">Database</a> | <a href="{{ url_for('clips_index') }}">Clips</a></p>
    <button type="button" id="debug-toggle" class="debug-toggle">Debug: Off</button>
  </div>
  <h1 class="debug-only">{{ film["title"] }}</h1>
  <p class="meta debug-only">film_id={{ film["id"] }} pipeline={{ pipeline_status }}</p>
  {% if skim_job and skim_job["status"] in ("queued", "running") %}
    <div class="panel debug-only">
      <strong>Skim preview is building.</strong>
      <div class="small" id="skim-job-text">{{ skim_job["status_text"] }}</div>
      <div style="height:12px;background:#e6d7c6;margin-top:8px;position:relative;">
        <div id="skim-job-bar" style="height:12px;background:#7a241c;width:{{ skim_job['progress_percent'] }}%;"></div>
      </div>
    </div>
  {% endif %}
  {% if clip_job and clip_job["status"] in ("queued", "running") %}
    <div class="panel debug-only">
      <strong>Clip is building.</strong>
      <div class="small" id="clip-job-text">{{ clip_job["status_text"] }}</div>
      <div style="height:12px;background:#e6d7c6;margin-top:8px;position:relative;">
        <div id="clip-job-bar" style="height:12px;background:#7a241c;width:{{ clip_job['progress_percent'] }}%;"></div>
      </div>
    </div>
  {% endif %}
  <div class="panel">
    <div class="metadata-row">
      <div>
        <div class="small">Film</div>
        <div class="metadata-title">{{ film["title"] }}</div>
      </div>
      <div>
        <div class="small" style="margin-bottom:8px;">Available Metadata</div>
        <div class="metadata-box">
          {% for item in film_metadata %}
            <div class="metadata-item">
              <div class="metadata-label">{{ item["label"] }}</div>
              <div class="metadata-value">{{ item["value"] }}</div>
            </div>
          {% endfor %}
        </div>
      </div>
    </div>
  </div>
  <div class="panel">
    <h2 class="debug-only">Skim Review</h2>
    <form method="post" action="{{ url_for('build_skim', film_id=film['id']) }}" class="debug-only">
      <label>Sample every seconds <input type="number" step="1" min="1" name="sample_every_seconds" value="{{ skim.sample_every_seconds if skim else 4 }}"></label>
      <label>Output fps <input type="number" step="1" min="1" name="output_fps" value="{{ skim.output_fps if skim else 12 }}"></label>
      <button type="submit">Build / Refresh Skim Preview</button>
    </form>
    {% if skim %}
      <p class="small debug-only">Move the mouse horizontally over the video to scrub. Click the video to add a mark. Use left/right arrow keys for frame stepping.</p>
      <div class="skim-shell">
        <div class="skim-viewport">
          <video id="skim-video" preload="metadata" playsinline src="{{ url_for('media_file', kind='preview', relpath=skim.relpath) }}"></video>
          <div class="skim-overlay">
            <span id="skim-overlay-left" class="debug-only">skim 0.00s</span>
            <span id="skim-overlay-right" class="debug-only">source 0s</span>
          </div>
        </div>
      </div>
      <form method="post" action="{{ url_for('add_mark', film_id=film['id']) }}" id="mark-form" style="margin-top: 12px;">
        <input type="hidden" name="preview_seconds" id="preview-seconds" value="0">
        <input type="hidden" name="sample_index" id="sample-index" value="1">
        <input type="hidden" name="source_seconds" id="source-seconds" value="0">
        <input type="hidden" name="skim_path" value="{{ skim.path }}">
        <input type="hidden" name="skim_sample_every_seconds" value="{{ skim.sample_every_seconds }}">
        <input type="hidden" name="skim_output_fps" value="{{ skim.output_fps }}">
        <div class="row">
          <p class="small debug-only" id="preview-readout">Current skim second: 0.00 | sample index: 1 | source second: 0</p>
          <button type="submit">Mark Current Frame</button>
        </div>
      </form>
    {% else %}
      <p class="small">No skim preview built yet.</p>
    {% endif %}
  </div>
  <div class="panel">
    <div class="fold-section">
      <details id="skim-overview-details">
        <summary>Skim Overview</summary>
        <div class="fold-body">
          {% if skim %}
            <p class="small">All skim frames from first to last. Open this section to generate the grid.</p>
            <div id="skim-overview-status" class="skim-overview-status">Collapsed.</div>
            <div id="skim-overview-grid" class="skim-overview-grid"></div>
          {% else %}
            <p class="small">No skim preview built yet.</p>
          {% endif %}
        </div>
      </details>
    </div>
  </div>
  <div class="panel">
    <div class="fold-section">
      <details id="kiss-detector-details">
        <summary>Kiss Detector</summary>
        <div class="fold-body">
          {% if skim %}
            <p class="small">Runs the Roboflow workflow on every skim overview frame and shows the returned visual output.</p>
            <div class="action-row">
              <button type="button" id="kiss-detector-analyze">Analyze Frames</button>
              <button type="button" id="kiss-detector-analyze-collisions" class="ghost">Analyze Collisions</button>
              <button type="button" id="kiss-detector-cluster" class="ghost">Cluster Heads</button>
              <button type="button" id="kiss-detector-make-candidates" class="ghost">Make Kiss Candidates</button>
              <button type="button" id="kiss-detector-remove" class="ghost">Remove Frames</button>
              <label class="small" style="display:inline-flex; gap:8px; align-items:center;">
                Min size px
                <input type="number" id="kiss-detector-min-size" min="0" step="1" value="40" style="width:90px;">
              </label>
              <button type="button" id="kiss-detector-clear-workflow-cache" class="ghost">Clear Cache</button>
              <div class="small" style="display:inline-flex; gap:12px; align-items:center;">
                <label style="display:inline-flex; gap:6px; align-items:center;"><input type="radio" name="kiss-detector-filter" value="all" checked> All</label>
                <label style="display:inline-flex; gap:6px; align-items:center;"><input type="radio" name="kiss-detector-filter" value="collision"> Collisions</label>
                <label style="display:inline-flex; gap:6px; align-items:center;"><input type="radio" name="kiss-detector-filter" value="candidate"> Kiss Candidates</label>
              </div>
              <a id="kiss-detector-download-all" class="button-link ghost-link" href="{{ url_for('kiss_detector_download_all', film_id=film['id']) }}" aria-disabled="true">Download All Frames</a>
            </div>
            <div id="kiss-detector-status" class="skim-overview-status">Collapsed.</div>
            <div id="kiss-detector-grid" class="kiss-detector-grid"></div>
            <div id="kiss-detector-debug" class="debug-box" style="display:none;"></div>
          {% else %}
            <p class="small">No skim preview built yet.</p>
          {% endif %}
        </div>
      </details>
    </div>
  </div>
  <div class="panel">
    <h2>Marks & Clips</h2>
    {% if marks %}
      {% for mark in marks %}
        <div class="mark">
          <div class="meta">mark {{ mark["id"] }} | tag {{ mark["selected_tag"] or "untagged" }} <span class="debug-only">| sample {{ mark["sample_index"] }} | source {{ mark["source_seconds"] }}s</span></div>
          <div>{{ mark["note"] or "" }}</div>
          <form method="post" action="{{ url_for('update_mark_tag', mark_id=mark['id']) }}" class="tag-selector" style="margin-top:8px;">
            <input type="hidden" name="return_film_id" value="{{ film['id'] }}">
            <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
              {% for tag in ["kiss", "phone", "cry", "dance"] %}
                <label class="tag-option">
                  <input class="tag-radio js-autosubmit-tag" type="radio" name="selected_tag" value="{{ tag }}" {% if mark["selected_tag"] == tag or (not mark["selected_tag"] and tag == "kiss") %}checked{% endif %}>
                  <span class="ghost film-tag-button" data-tag="{{ tag }}">{{ tag }}</span>
                </label>
              {% endfor %}
            </div>
          </form>
          <form method="post" action="{{ url_for('build_clip', film_id=film['id'], mark_id=mark['id']) }}" style="margin-top:8px;">
            <label>Pre <input type="number" step="1" min="1" name="pre_seconds" value="20"></label>
            <label>Post <input type="number" step="1" min="1" name="post_seconds" value="20"></label>
            <button type="submit">Build Rough Clip</button>
          </form>
          <form method="post" action="{{ url_for('delete_mark_route', mark_id=mark['id']) }}" style="margin-top:8px;">
            <input type="hidden" name="return_film_id" value="{{ film['id'] }}">
            <button class="ghost" type="submit">Delete Mark</button>
          </form>
          {% if mark["clip_relpath"] %}
            <p><a href="{{ url_for('media_file', kind=mark['clip_kind'], relpath=mark['clip_relpath']) }}">Open clip</a></p>
            <video id="clip-video-{{ mark['clip_id'] }}" controls preload="metadata" src="{{ url_for('media_file', kind=mark['clip_kind'], relpath=mark['clip_relpath']) }}"></video>
            <form method="post" action="{{ url_for('update_clip_kiss_timing', clip_id=mark['clip_id']) }}" style="margin-top:8px;">
              <input type="hidden" name="return_film_id" value="{{ film['id'] }}">
              <input type="hidden" name="kiss_start_seconds" id="kiss-start-{{ mark['clip_id'] }}" value="{{ mark['kiss_start_seconds'] if mark['kiss_start_seconds'] is not none else '' }}">
              <input type="hidden" name="kiss_end_seconds" id="kiss-end-{{ mark['clip_id'] }}" value="{{ mark['kiss_end_seconds'] if mark['kiss_end_seconds'] is not none else '' }}">
              <div class="small" id="kiss-timing-view-{{ mark['clip_id'] }}">
                Kiss start: {{ mark['kiss_start_seconds'] if mark['kiss_start_seconds'] is not none else '-' }} |
                Kiss end: {{ mark['kiss_end_seconds'] if mark['kiss_end_seconds'] is not none else '-' }}
              </div>
              <div class="row">
                <button class="ghost js-go-to-kiss" type="button" data-clip-id="{{ mark['clip_id'] }}">Go To Kiss</button>
                <button class="ghost js-set-kiss-time" type="button" data-clip-id="{{ mark['clip_id'] }}" data-target="start">Set Kiss Start</button>
                <button class="ghost js-set-kiss-time" type="button" data-clip-id="{{ mark['clip_id'] }}" data-target="end">Set Kiss End</button>
                <button class="ghost" type="submit">Save Kiss Timing</button>
              </div>
            </form>
            <form method="post" action="{{ url_for('delete_clip_route', clip_id=mark['clip_id']) }}" style="margin-top:8px;">
              <input type="hidden" name="return_film_id" value="{{ film['id'] }}">
              <button class="ghost" type="submit">Delete Clip</button>
            </form>
            <form method="post" action="{{ url_for('toggle_ignore_clip_route', clip_id=mark['clip_id']) }}" style="margin-top:8px;">
              <input type="hidden" name="return_film_id" value="{{ film['id'] }}">
              <button class="ghost" type="submit" title="{{ 'Unignore clip' if mark['clip_ignored'] else 'Ignore clip' }}">
                {{ '🙈' if mark['clip_ignored'] else '🚫' }}
              </button>
            </form>
          {% endif %}
        </div>
      {% endfor %}
    {% else %}
      <p class="small">No marks yet.</p>
    {% endif %}
  </div>
  <div class="panel">
    <div class="row">
      <form method="post" action="{{ url_for('finalize_review', film_id=film['id']) }}">
        <input type="hidden" name="action" value="no_kiss">
        <input type="hidden" name="clip_timings_json" class="js-clip-timings-json" value="">
        <button class="ghost" type="submit">Reviewed No Kiss</button>
      </form>
      <form method="post" action="{{ url_for('finalize_review', film_id=film['id']) }}">
        <input type="hidden" name="action" value="has_kiss">
        <input type="hidden" name="clip_timings_json" class="js-clip-timings-json" value="">
        <button type="submit">Reviewed</button>
      </form>
    </div>
  </div>
<script>
  const debugToggle = document.getElementById("debug-toggle");
  const debugStorageKey = "ia-kissing-debug-hidden";
  const applyDebugMode = (hidden) => {
    document.body.classList.toggle("debug-off", hidden);
    if (debugToggle) {
      debugToggle.textContent = hidden ? "Debug: Off" : "Debug: On";
    }
  };
  const storedValue = window.localStorage.getItem(debugStorageKey);
  applyDebugMode(storedValue === null ? true : storedValue === "1");
  if (debugToggle) {
    debugToggle.addEventListener("click", () => {
      const nextHidden = !document.body.classList.contains("debug-off");
      window.localStorage.setItem(debugStorageKey, nextHidden ? "1" : "0");
      applyDebugMode(nextHidden);
    });
  }
  document.querySelectorAll(".js-autosubmit-tag").forEach((input) => {
    input.addEventListener("change", () => {
      const form = input.closest("form");
      if (form) {
        form.requestSubmit();
      }
    });
  });
  document.querySelectorAll(".js-set-kiss-time").forEach((button) => {
    button.addEventListener("click", () => {
      const clipId = button.dataset.clipId;
      const target = button.dataset.target;
      const video = document.getElementById(`clip-video-${clipId}`);
      const input = document.getElementById(`kiss-${target}-${clipId}`);
      const view = document.getElementById(`kiss-timing-view-${clipId}`);
      if (!video || !input || !view) return;
      input.value = (video.currentTime || 0).toFixed(3);
      const start = document.getElementById(`kiss-start-${clipId}`)?.value || "-";
      const end = document.getElementById(`kiss-end-${clipId}`)?.value || "-";
      view.textContent = `Kiss start: ${start} | Kiss end: ${end}`;
    });
  });
  document.querySelectorAll(".js-go-to-kiss").forEach((button) => {
    button.addEventListener("click", () => {
      const clipId = button.dataset.clipId;
      const video = document.getElementById(`clip-video-${clipId}`);
      const startInput = document.getElementById(`kiss-start-${clipId}`);
      if (!video || !startInput) return;
      const seconds = Number.parseFloat(startInput.value);
      if (!Number.isFinite(seconds)) return;
      video.currentTime = Math.max(0, seconds);
      if (video.paused) {
        video.play().catch(() => {});
      }
    });
  });
  document.querySelectorAll('form[action$="/finalize"]').forEach((form) => {
    form.addEventListener("submit", () => {
      const payload = [];
      document.querySelectorAll('input[id^="kiss-start-"]').forEach((startInput) => {
        const clipId = startInput.id.replace("kiss-start-", "");
        const endInput = document.getElementById(`kiss-end-${clipId}`);
        payload.push({
          clip_id: clipId,
          kiss_start_seconds: startInput.value || "",
          kiss_end_seconds: endInput ? endInput.value || "" : "",
        });
      });
      const target = form.querySelector(".js-clip-timings-json");
      if (target) {
        target.value = JSON.stringify(payload);
      }
    });
  });
</script>
{% if skim %}
<script>
  const video = document.getElementById("skim-video");
  const markForm = document.getElementById("mark-form");
  const skimOverviewDetails = document.getElementById("skim-overview-details");
  const skimOverviewGrid = document.getElementById("skim-overview-grid");
  const skimOverviewStatus = document.getElementById("skim-overview-status");
  const kissDetectorDetails = document.getElementById("kiss-detector-details");
  const kissDetectorGrid = document.getElementById("kiss-detector-grid");
  const kissDetectorStatus = document.getElementById("kiss-detector-status");
  const kissDetectorDebug = document.getElementById("kiss-detector-debug");
  const kissDetectorAnalyzeButton = document.getElementById("kiss-detector-analyze");
  const kissDetectorAnalyzeCollisionsButton = document.getElementById("kiss-detector-analyze-collisions");
  const kissDetectorClusterButton = document.getElementById("kiss-detector-cluster");
  const kissDetectorMakeCandidatesButton = document.getElementById("kiss-detector-make-candidates");
  const kissDetectorRemoveButton = document.getElementById("kiss-detector-remove");
  const kissDetectorMinSizeInput = document.getElementById("kiss-detector-min-size");
  const kissDetectorClearWorkflowCacheButton = document.getElementById("kiss-detector-clear-workflow-cache");
  const kissDetectorFilterRadios = Array.from(document.querySelectorAll('input[name="kiss-detector-filter"]'));
  const kissDetectorDownloadAll = document.getElementById("kiss-detector-download-all");
  const previewInput = document.getElementById("preview-seconds");
  const sampleInput = document.getElementById("sample-index");
  const sourceInput = document.getElementById("source-seconds");
  const readout = document.getElementById("preview-readout");
  const overlayLeft = document.getElementById("skim-overlay-left");
  const overlayRight = document.getElementById("skim-overlay-right");
  const sampleEvery = {{ skim.sample_every_seconds }};
  const outputFps = {{ skim.output_fps }};
  const frameDuration = 1 / outputFps;
  const seekEpsilon = Math.max(0.001, frameDuration / 2);
  let lastTapAt = 0;
  let skimOverviewBuilt = false;
  let skimOverviewBuilding = false;
  let latestKissDetectorFrames = [];
  let kissDetectorPollingTimer = null;
  let kissDetectorRequestInFlight = false;
  const workflowCacheBypassNextStorageKey = "ia-kissing-roboflow-workflow-cache-bypass-next";
  let scrubFrameRequested = false;
  let pendingSeekTime = null;
  let seekInFlight = false;

  const updateFields = () => {
    const previewSeconds = video.currentTime || 0;
    const sampleIndex = Math.max(1, Math.floor(previewSeconds * outputFps) + 1);
    const sourceSeconds = (sampleIndex - 1) * sampleEvery;
    previewInput.value = previewSeconds.toFixed(3);
    sampleInput.value = sampleIndex;
    sourceInput.value = sourceSeconds.toFixed(3);
    readout.textContent = `Current skim second: ${previewSeconds.toFixed(2)} | sample index: ${sampleIndex} | source second: ${sourceSeconds.toFixed(0)}`;
    overlayLeft.textContent = `skim ${previewSeconds.toFixed(2)}s`;
    overlayRight.textContent = `source ${sourceSeconds.toFixed(0)}s | frame ${sampleIndex}`;
  };

  const scrubToVideoX = (clientX) => {
    const rect = video.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const duration = video.duration || 0;
    if (!Number.isFinite(duration) || duration <= 0) {
      return;
    }
    pendingSeekTime = duration * ratio;
    updateFieldsForPendingSeek();
    flushPendingSeek();
  };

  const updateFieldsForPendingSeek = () => {
    if (pendingSeekTime === null) {
      updateFields();
      return;
    }
    const previewSeconds = pendingSeekTime;
    const sampleIndex = Math.max(1, Math.floor(previewSeconds * outputFps) + 1);
    const sourceSeconds = (sampleIndex - 1) * sampleEvery;
    previewInput.value = previewSeconds.toFixed(3);
    sampleInput.value = sampleIndex;
    sourceInput.value = sourceSeconds.toFixed(3);
    readout.textContent = `Current skim second: ${previewSeconds.toFixed(2)} | sample index: ${sampleIndex} | source second: ${sourceSeconds.toFixed(0)}`;
    overlayLeft.textContent = `skim ${previewSeconds.toFixed(2)}s`;
    overlayRight.textContent = `source ${sourceSeconds.toFixed(0)}s | frame ${sampleIndex}`;
  };

  const flushPendingSeek = () => {
    if (scrubFrameRequested) {
      return;
    }
    scrubFrameRequested = true;
    window.requestAnimationFrame(() => {
      scrubFrameRequested = false;
      if (seekInFlight || pendingSeekTime === null) {
        return;
      }
      const nextSeekTime = pendingSeekTime;
      pendingSeekTime = null;
      if (Math.abs((video.currentTime || 0) - nextSeekTime) <= seekEpsilon) {
        updateFields();
        if (pendingSeekTime !== null) {
          flushPendingSeek();
        }
        return;
      }
      seekInFlight = true;
      video.currentTime = nextSeekTime;
    });
  };

  video.addEventListener("mousemove", (event) => {
    scrubToVideoX(event.clientX);
  });
  video.addEventListener("click", (event) => {
    event.preventDefault();
    scrubToVideoX(event.clientX);
    markForm.requestSubmit();
  });
  video.addEventListener("touchmove", (event) => {
    if (!event.touches.length) return;
    event.preventDefault();
    scrubToVideoX(event.touches[0].clientX);
  }, { passive: false });
  video.addEventListener("touchstart", (event) => {
    if (!event.touches.length) return;
    const now = Date.now();
    const clientX = event.touches[0].clientX;
    scrubToVideoX(clientX);
    if (now - lastTapAt < 300) {
      event.preventDefault();
      markForm.requestSubmit();
    }
    lastTapAt = now;
  }, { passive: false });
  video.addEventListener("loadedmetadata", updateFields);
  video.addEventListener("seeked", () => {
    seekInFlight = false;
    updateFields();
    if (pendingSeekTime !== null && Math.abs((video.currentTime || 0) - pendingSeekTime) > 0.001) {
      flushPendingSeek();
    }
  });
  video.addEventListener("timeupdate", () => {
    if (!seekInFlight) {
      return;
    }
    seekInFlight = false;
    updateFields();
    if (pendingSeekTime !== null && Math.abs((video.currentTime || 0) - pendingSeekTime) > seekEpsilon) {
      flushPendingSeek();
    }
  });

  const buildSkimOverview = async () => {
    if (skimOverviewBuilt || skimOverviewBuilding || !skimOverviewGrid || !skimOverviewStatus) return;
    skimOverviewBuilding = true;
    skimOverviewStatus.textContent = "Generating frame grid...";
    skimOverviewGrid.innerHTML = "";
    try {
      const response = await fetch("{{ url_for('skim_overview_payload', film_id=film['id']) }}");
      if (!response.ok) {
        throw new Error(`overview request failed: ${response.status}`);
      }
      const payload = await response.json();
      payload.frames.forEach((frame) => {
        const card = document.createElement("div");
        card.className = "skim-frame-card";

        const image = document.createElement("img");
        image.src = frame.media_url;
        image.alt = `Skim frame ${frame.index}`;

        const meta = document.createElement("div");
        meta.className = "skim-frame-meta";
        meta.textContent = `frame ${frame.index} | source ${frame.source_seconds}s`;

        const actions = document.createElement("div");
        actions.className = "skim-frame-actions";

        const downloadLink = document.createElement("a");
        downloadLink.href = frame.media_url;
        downloadLink.download = `skim-frame-${String(frame.index).padStart(6, "0")}.jpg`;
        downloadLink.textContent = "download";

        card.appendChild(image);
        card.appendChild(meta);
        actions.appendChild(downloadLink);
        card.appendChild(actions);
        skimOverviewGrid.appendChild(card);
      });

      skimOverviewBuilt = true;
      skimOverviewStatus.textContent = `${payload.frames.length} skim frames`;
    } catch (_error) {
      skimOverviewStatus.textContent = "Could not generate skim overview.";
    } finally {
      skimOverviewBuilding = false;
    }
  };

  if (skimOverviewDetails) {
    skimOverviewDetails.addEventListener("toggle", () => {
      if (skimOverviewDetails.open) {
        buildSkimOverview();
      }
    });
  }

  const stopKissDetectorPolling = () => {
    if (kissDetectorPollingTimer !== null) {
      window.clearTimeout(kissDetectorPollingTimer);
      kissDetectorPollingTimer = null;
    }
  };

  const updateKissDetectorDownloadState = (hasFrames) => {
    if (!kissDetectorDownloadAll) return;
    kissDetectorDownloadAll.setAttribute("aria-disabled", hasFrames ? "false" : "true");
  };

  const updateWorkflowCacheButtonState = () => {
    if (!kissDetectorClearWorkflowCacheButton) return;
    const bypassNext = window.localStorage.getItem(workflowCacheBypassNextStorageKey) === "1";
    kissDetectorClearWorkflowCacheButton.textContent = bypassNext ? "Cache Will Be Bypassed" : "Clear Cache";
  };

  if (kissDetectorClearWorkflowCacheButton) {
    updateWorkflowCacheButtonState();
    kissDetectorClearWorkflowCacheButton.addEventListener("click", () => {
      window.localStorage.setItem(workflowCacheBypassNextStorageKey, "1");
      updateWorkflowCacheButtonState();
      if (kissDetectorStatus) {
        kissDetectorStatus.textContent = "Workflow cache will be bypassed on the next Analyze Frames run.";
      }
    });
  }

  const getKissDetectorFilter = () => {
    const selected = kissDetectorFilterRadios.find((radio) => radio.checked);
    return selected ? selected.value : "all";
  };

  const renderKissDetectorFrames = (frames, reset = false) => {
    if (!kissDetectorGrid) return;
    kissDetectorGrid.innerHTML = "";
    const filterMode = getKissDetectorFilter();
    const visibleFrames = frames.filter((frame) => {
      if (filterMode === "collision") {
        return Boolean(frame.collision);
      }
      if (filterMode === "candidate") {
        return Boolean(frame.kiss_candidate);
      }
      return true;
    });
    visibleFrames.forEach((frame) => {
      const card = document.createElement("div");
      card.className = "skim-frame-card";

      const image = document.createElement("img");
      image.src = frame.media_url;
      image.alt = `Kiss detector frame ${frame.index}`;

      const meta = document.createElement("div");
      meta.className = "skim-frame-meta";
      meta.textContent = `frame ${frame.index} | source ${frame.source_seconds}s${frame.collision ? " | collision: true" : ""}${frame.kiss_candidate ? " | kiss candidate: true" : ""}`;

      const actions = document.createElement("div");
      actions.className = "skim-frame-actions";

      const pngLink = document.createElement("a");
      pngLink.href = frame.media_url;
      pngLink.download = `kiss-detector-${String(frame.index).padStart(6, "0")}.png`;
      pngLink.textContent = "get png";

      const jsonLink = document.createElement("a");
      jsonLink.href = frame.predictions_url;
      jsonLink.download = `kiss-detector-${String(frame.index).padStart(6, "0")}.json`;
      jsonLink.textContent = "get json";

      card.appendChild(image);
      card.appendChild(meta);
      actions.appendChild(pngLink);
      if (frame.predictions_url) {
        actions.appendChild(jsonLink);
      }
      card.appendChild(actions);
      kissDetectorGrid.appendChild(card);
    });
    updateKissDetectorDownloadState(frames.length > 0);
  };

  const applyKissDetectorStatus = (payload) => {
    latestKissDetectorFrames = payload.frames || [];
    renderKissDetectorFrames(latestKissDetectorFrames, true);
    if (kissDetectorDebug) {
      if (payload.debug) {
        kissDetectorDebug.style.display = "block";
        kissDetectorDebug.textContent = payload.debug;
      } else {
        kissDetectorDebug.style.display = "none";
        kissDetectorDebug.textContent = "";
      }
    }
    const counts = Number.isFinite(payload.total) && payload.total > 0
      ? `${payload.completed}/${payload.total} saved`
      : `${payload.completed || 0} saved`;
    const statusText = payload.error || payload.status_text || "Idle";
    kissDetectorStatus.textContent = `${counts} | ${statusText}`;
    const active = payload.status === "queued" || payload.status === "running";
    if (kissDetectorAnalyzeButton) {
      kissDetectorAnalyzeButton.disabled = false;
      kissDetectorAnalyzeButton.dataset.mode = active ? "stop" : "start";
      kissDetectorAnalyzeButton.textContent = active ? "Stop Analyzing" : "Analyze Frames";
    }
    if (kissDetectorRemoveButton) {
      kissDetectorRemoveButton.disabled = false;
    }
    if (kissDetectorAnalyzeCollisionsButton) {
      kissDetectorAnalyzeCollisionsButton.disabled = active || latestKissDetectorFrames.length === 0;
    }
    if (kissDetectorClusterButton) {
      kissDetectorClusterButton.disabled = active || latestKissDetectorFrames.length === 0;
    }
    if (kissDetectorMakeCandidatesButton) {
      kissDetectorMakeCandidatesButton.disabled = active || latestKissDetectorFrames.length === 0;
    }
    if (active && kissDetectorDetails?.open) {
      stopKissDetectorPolling();
      kissDetectorPollingTimer = window.setTimeout(loadKissDetectorStatus, 1500);
    } else {
      stopKissDetectorPolling();
    }
  };

  const loadKissDetectorStatus = async () => {
    if (kissDetectorRequestInFlight || !kissDetectorStatus) return;
    kissDetectorRequestInFlight = true;
    try {
      const response = await fetch("{{ url_for('kiss_detector_payload', film_id=film['id']) }}");
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `kiss detector request failed: ${response.status}`);
      }
      applyKissDetectorStatus(payload);
    } catch (error) {
      kissDetectorStatus.textContent = error instanceof Error ? error.message : "Could not load kiss detector status.";
    } finally {
      kissDetectorRequestInFlight = false;
    }
  };

  if (kissDetectorDetails) {
    kissDetectorDetails.addEventListener("toggle", () => {
      if (kissDetectorDetails.open) {
        loadKissDetectorStatus();
      } else {
        stopKissDetectorPolling();
      }
    });
  }

  if (kissDetectorAnalyzeButton) {
    kissDetectorAnalyzeButton.addEventListener("click", async () => {
      try {
        const isActive = kissDetectorAnalyzeButton.dataset.mode === "stop";
        kissDetectorAnalyzeButton.disabled = true;
        kissDetectorStatus.textContent = isActive ? "Stopping kiss detector job..." : "Queueing kiss detector job...";
        const response = await fetch(
          isActive
            ? "{{ url_for('kiss_detector_stop', film_id=film['id']) }}"
            : "{{ url_for('kiss_detector_analyze', film_id=film['id']) }}",
          isActive
            ? { method: "POST" }
            : {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  use_workflow_cache: window.localStorage.getItem(workflowCacheBypassNextStorageKey) !== "1",
                }),
              },
        );
        if (!isActive && window.localStorage.getItem(workflowCacheBypassNextStorageKey) === "1") {
          window.localStorage.removeItem(workflowCacheBypassNextStorageKey);
          updateWorkflowCacheButtonState();
        }
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `kiss detector request failed: ${response.status}`);
        }
        applyKissDetectorStatus(payload);
      } catch (error) {
        kissDetectorStatus.textContent = error instanceof Error ? error.message : "Could not update kiss detector job.";
        kissDetectorAnalyzeButton.disabled = false;
      }
    });
  }

  if (kissDetectorAnalyzeCollisionsButton) {
    kissDetectorAnalyzeCollisionsButton.addEventListener("click", async () => {
      kissDetectorAnalyzeCollisionsButton.disabled = true;
      kissDetectorStatus.textContent = "Analyzing collision polygons...";
      try {
        const response = await fetch("{{ url_for('kiss_detector_analyze_collisions', film_id=film['id']) }}", { method: "POST" });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `collision analysis failed: ${response.status}`);
        }
        applyKissDetectorStatus(payload);
      } catch (error) {
        kissDetectorStatus.textContent = error instanceof Error ? error.message : "Could not analyze collisions.";
        kissDetectorAnalyzeCollisionsButton.disabled = false;
      }
    });
  }

  if (kissDetectorClusterButton) {
    kissDetectorClusterButton.addEventListener("click", async () => {
      kissDetectorClusterButton.disabled = true;
      kissDetectorStatus.textContent = "Clustering duplicate head masks...";
      const minSizePixels = Math.max(0, Number.parseInt(kissDetectorMinSizeInput?.value || "0", 10) || 0);
      try {
        const response = await fetch("{{ url_for('kiss_detector_cluster', film_id=film['id']) }}", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ min_size_pixels: minSizePixels }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `cluster analysis failed: ${response.status}`);
        }
        applyKissDetectorStatus(payload);
      } catch (error) {
        kissDetectorStatus.textContent = error instanceof Error ? error.message : "Could not cluster duplicate masks.";
        kissDetectorClusterButton.disabled = false;
      }
    });
  }

  if (kissDetectorMakeCandidatesButton) {
      kissDetectorMakeCandidatesButton.addEventListener("click", async () => {
      kissDetectorMakeCandidatesButton.disabled = true;
      kissDetectorStatus.textContent = "Making kiss candidates...";
      const minSizePixels = Math.max(0, Number.parseInt(kissDetectorMinSizeInput?.value || "0", 10) || 0);
      try {
        const response = await fetch("{{ url_for('kiss_detector_make_candidates', film_id=film['id']) }}", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ min_size_pixels: minSizePixels }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `candidate analysis failed: ${response.status}`);
        }
        applyKissDetectorStatus(payload);
      } catch (error) {
        kissDetectorStatus.textContent = error instanceof Error ? error.message : "Could not make kiss candidates.";
        kissDetectorMakeCandidatesButton.disabled = false;
      }
    });
  }

  if (kissDetectorFilterRadios.length) {
    kissDetectorFilterRadios.forEach((radio) => radio.addEventListener("change", () => {
      renderKissDetectorFrames(latestKissDetectorFrames, true);
    }));
  }

  if (kissDetectorRemoveButton) {
    kissDetectorRemoveButton.addEventListener("click", async () => {
      stopKissDetectorPolling();
      try {
        const response = await fetch("{{ url_for('kiss_detector_remove', film_id=film['id']) }}", { method: "POST" });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `kiss detector remove failed: ${response.status}`);
        }
        latestKissDetectorFrames = [];
        renderKissDetectorFrames([], true);
        applyKissDetectorStatus(payload);
      } catch (error) {
        kissDetectorStatus.textContent = error instanceof Error ? error.message : "Could not remove kiss detector frames.";
      }
    });
  }

  window.addEventListener("keydown", (event) => {
    const active = document.activeElement;
    if (active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT")) {
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      video.currentTime = Math.max(0, (video.currentTime || 0) - frameDuration);
      updateFields();
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      const duration = video.duration || 0;
      video.currentTime = Math.min(duration, (video.currentTime || 0) + frameDuration);
      updateFields();
    }
  });
</script>
{% endif %}
{% if skim_job and skim_job["status"] in ("queued", "running") %}
<script>
  const pollSkim = async () => {
    const response = await fetch("{{ url_for('skim_status', film_id=film['id']) }}");
    const payload = await response.json();
    const bar = document.getElementById("skim-job-bar");
    const text = document.getElementById("skim-job-text");
    if (bar) bar.style.width = `${payload.progress_percent}%`;
    if (text) text.textContent = payload.status_text;
    if (payload.status === "done") {
      window.location = "{{ url_for('film_detail', film_id=film['id']) }}";
      return;
    }
    if (payload.status === "error") {
      if (text) text.textContent = payload.status_text;
      return;
    }
    window.setTimeout(pollSkim, 1500);
  };
  window.setTimeout(pollSkim, 1500);
</script>
{% endif %}
{% if clip_job and clip_job["status"] in ("queued", "running") %}
<script>
  const pollClip = async () => {
    const response = await fetch("{{ url_for('clip_status', film_id=film['id']) }}");
    const payload = await response.json();
    const bar = document.getElementById("clip-job-bar");
    const text = document.getElementById("clip-job-text");
    if (bar) bar.style.width = `${payload.progress_percent}%`;
    if (text) text.textContent = payload.status_text;
    if (payload.status === "done") {
      window.location = "{{ url_for('film_detail', film_id=film['id']) }}";
      return;
    }
    if (payload.status === "error") {
      if (text) text.textContent = payload.status_text;
      return;
    }
    window.setTimeout(pollClip, 1500);
  };
  window.setTimeout(pollClip, 1500);
</script>
{% endif %}
</body>
</html>
"""


CLIPS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Saved Clips</title>
  <style>
    body { margin: 16px; background: #111; color: #f3eee7; }
    .grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
    .tile { background: #000; aspect-ratio: 16 / 9; overflow: hidden; }
    .tile video { width: 100%; height: 100%; object-fit: cover; display: block; cursor: pointer; background: #000; }
    @media (max-width: 1400px) { .grid { grid-template-columns: repeat(5, minmax(0, 1fr)); } }
    @media (max-width: 1100px) { .grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
    @media (max-width: 800px) { .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
    @media (max-width: 560px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  </style>
</head>
<body>
  {% if clips %}
    <div class="grid">
      {% for clip in clips %}
        <div class="tile">
          <video preload="metadata" playsinline src="{{ url_for('media_file', kind=clip['kind'], relpath=clip['relpath']) }}"></video>
        </div>
      {% endfor %}
    </div>
  {% else %}
    <div>No clips yet.</div>
  {% endif %}
<script>
  document.querySelectorAll(".tile video").forEach((video) => {
    video.removeAttribute("controls");
    video.addEventListener("click", () => {
      if (video.paused) {
        video.play();
      } else {
        video.pause();
      }
    });
  });
</script>
</body>
</html>
"""


REVIEW_DATA_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Review Data</title>
  <style>
    :root { color-scheme: dark; --bg: #0d1117; --panel: #151b23; --panel-2: #111720; --border: #263244; --text: #edf3ff; --muted: #93a4bc; }
    body { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; margin: 24px; background: radial-gradient(circle at top, #182334 0%, var(--bg) 55%); color: var(--text); }
    a { color: #7cc7ff; text-decoration: none; }
    .section { background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%); border: 1px solid var(--border); border-radius: 18px; padding: 16px; margin-bottom: 18px; }
    .section summary { cursor: pointer; list-style: none; }
    .section summary::-webkit-details-marker { display: none; }
    .section h2 { margin: 0 0 12px 0; display: inline-block; }
    .section summary::after { content: "show"; float: right; color: var(--muted); font-size: 13px; margin-top: 6px; }
    .section[open] summary::after { content: "hide"; }
    .meta { color: var(--muted); font-size: 13px; margin-bottom: 12px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .card { background: #0b1016; border: 1px solid var(--border); border-radius: 14px; overflow: hidden; }
    .card video { width: 100%; display: block; background: #000; aspect-ratio: 16 / 9; }
    .card-body { padding: 10px; }
    .path { font-size: 12px; color: var(--muted); word-break: break-all; margin-bottom: 8px; }
    .row { display: flex; gap: 8px; align-items: center; justify-content: space-between; margin-bottom: 8px; }
    .badge { display: inline-block; padding: 4px 8px; border-radius: 999px; border: 1px solid #324054; background: #1b2430; color: #c8d5e6; font-size: 12px; }
    .badge.ignored { background: #252d37; border-color: #3e4d61; color: #94a4bd; }
    .delete-button { padding: 6px 10px; line-height: 1; background: #3a2025; color: #ffd3db; border: 1px solid #8d4b58; border-radius: 10px; cursor: pointer; }
    .empty { color: var(--muted); }
    @media (max-width: 1200px) { .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
    @media (max-width: 900px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } body { margin: 14px; } }
    @media (max-width: 560px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <p><a href="{{ url_for('index') }}">Next Review</a> | <a href="{{ url_for('films_index') }}">Database</a> | <a href="{{ url_for('clips_index') }}">Clips</a></p>
  <h1>Review Data</h1>
  {% for section in sections %}
    <details class="section" {% if section["open"] %}open{% endif %}>
      <summary><h2>{{ section["title"] }}</h2></summary>
      <div class="meta">{{ section["count"] }} video file{{ '' if section["count"] == 1 else 's' }}{% if section["root"] %} in {{ section["root"] }}{% endif %}</div>
      {% if section["items"] %}
        <div class="grid">
          {% for item in section["items"] %}
            <div class="card">
              {% if item["playable"] %}
                <video preload="metadata" controls src="{{ item['media_url'] }}"></video>
              {% else %}
                <div class="card-body">
                  <div class="empty">Playback disabled</div>
                </div>
              {% endif %}
              <div class="card-body">
                <div class="row">
                  <span class="badge">{{ item["size_text"] }}</span>
                  <span class="badge {{ 'ignored' if item['status_kind'] != 'pending' else '' }}">{{ item["status_text"] }}</span>
                  {% if item["ignored"] %}
                    <span class="badge ignored">ignored clip</span>
                  {% endif %}
                </div>
                <div class="path">{{ item["display_path"] }}</div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                  {% if item["film_id"] %}
                    <form method="post" action="{{ url_for('requeue_review_data_movie') }}">
                      <input type="hidden" name="film_id" value="{{ item['film_id'] }}">
                      <button class="delete-button" type="submit" style="background:#132c49;border-color:#2b6aa7;color:#91ccff;">Requeue movie</button>
                    </form>
                  {% endif %}
                  {% if item["can_delete"] %}
                    <form method="post" action="{{ url_for('delete_review_data_file') }}">
                      <input type="hidden" name="kind" value="{{ item['kind'] }}">
                      <input type="hidden" name="relpath" value="{{ item['relpath'] }}">
                      <button class="delete-button" type="submit">Delete video file</button>
                    </form>
                  {% endif %}
                </div>
              </div>
            </div>
          {% endfor %}
        </div>
      {% else %}
        <div class="empty">No video files in this section.</div>
      {% endif %}
    </details>
  {% endfor %}
</body>
</html>
"""


def _format_film_metadata_value(value) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                return value
            if isinstance(decoded, list):
                return ", ".join(str(item) for item in decoded if str(item).strip())
            if isinstance(decoded, dict):
                return "; ".join(f"{key}: {decoded[key]}" for key in sorted(decoded))
        return value
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return "; ".join(f"{key}: {value[key]}" for key in sorted(value))
    return str(value)


def _build_film_metadata_payload(film: sqlite3.Row) -> list[dict[str, str]]:
    items = []
    for key, value in dict(film).items():
        if key == "title" or value is None:
            continue
        formatted = _format_film_metadata_value(value)
        if not formatted:
            continue
        label = key.replace("_", " ")
        items.append({"label": label, "value": formatted})
    return items


def create_app() -> Flask:
    app = Flask(__name__)
    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    cors_allowed_origins = {"http://localhost:3000", "http://127.0.0.1:3000"}
    cors_origin_patterns = [
        re.compile(r"^http://10\.73\.73\.\d{1,3}:3000$"),
    ]

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin")
        origin_allowed = origin in cors_allowed_origins or (
            isinstance(origin, str) and any(pattern.match(origin) for pattern in cors_origin_patterns)
        )
        if origin_allowed and (
            request.path.startswith("/api/") or request.path.startswith("/media/")
        ):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
            response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Content-Type"
        return response

    @app.get("/")
    def index():
        with get_connection(settings.db_path) as conn:
            next_film = _get_next_ready_film(conn)
            if next_film:
                return redirect(url_for("film_detail", film_id=next_film["id"]))
            queue_job = _load_download_batch_job(conn)
            ready_count = _count_active_pool_films(conn)
        return render_template_string(
            EMPTY_TEMPLATE,
            ready_count=ready_count,
            target_ready=READY_TARGET,
            queue_status=queue_job["status_text"] if queue_job else "idle",
        )

    @app.get("/films")
    def films_index():
        active_filter_tag = request.args.get("tag", type=str)
        films = _load_film_rows(settings, active_filter_tag)
        return render_template_string(
            FILMS_TEMPLATE,
            films=films,
            tag_stats=_load_global_tag_stats(settings),
            active_filter_tag=active_filter_tag,
            clip_order_mode=_get_clip_order_mode(settings),
        )

    @app.get("/films/status")
    def films_status():
        active_filter_tag = request.args.get("tag", type=str)
        return jsonify({"films": _load_film_rows(settings, active_filter_tag)})

    @app.post("/films/clip-order-mode")
    def set_clip_order_mode():
        mode = request.form.get("mode", "").strip()
        if mode not in {"random", "ordered"}:
            abort(400)
        _set_clip_order_mode(settings, mode)
        return redirect(url_for("films_index", tag=request.args.get("tag") or request.form.get("tag") or None))

    @app.get("/films/<int:film_id>/clips")
    def film_clips_payload(film_id: int):
        tag = request.args.get("tag", type=str)
        with get_connection(settings.db_path) as conn:
            clips = _load_clips(conn, settings.clips_dir, film_id=film_id, tag=tag)
        payload = [
            {
                "id": clip["id"],
                "tag": clip.get("clip_tag"),
                "relpath": clip["relpath"],
                "kind": clip["kind"],
            }
            for clip in clips
        ]
        return jsonify({"clips": payload})

    @app.get("/films/<int:film_id>")
    def film_detail(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            _reconcile_stale_skim_job(conn, film_id)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
            marks = _load_marks(conn, settings.clips_dir, film_id)
            skim_job = _load_latest_job(conn, film_id, "build_skim_preview")
            clip_job = _load_latest_job(conn, film_id, "build_manual_clip")
            kiss_detector_job = _load_latest_job(conn, film_id, "kiss_detector")
            review = _get_review_state(conn, film_id)
            source_cached = _source_cached(settings, film["archive_identifier"])
        return render_template_string(
            FILM_TEMPLATE,
            film=film,
            film_metadata=_build_film_metadata_payload(film),
            skim=skim,
            marks=marks,
            skim_job=skim_job,
            clip_job=clip_job,
            kiss_detector_job=kiss_detector_job,
            pipeline_status=_display_pipeline_status(dict(film), skim_job, review, source_cached),
        )

    @app.post("/films/<int:film_id>/build-skim")
    def build_skim(film_id: int):
        sample_every_seconds = float(request.form.get("sample_every_seconds", 4))
        output_fps = int(request.form.get("output_fps", 12))
        _queue_build_skim(settings, film_id, sample_every_seconds=sample_every_seconds, output_fps=output_fps)
        return redirect(url_for("film_detail", film_id=film_id))

    @app.post("/films/<int:film_id>/marks")
    def add_mark(film_id: int):
        with get_connection(settings.db_path) as conn:
            conn.execute(
                """
                INSERT INTO manual_marks (
                    film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                    preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    film_id,
                    request.form.get("skim_path"),
                    float(request.form.get("skim_sample_every_seconds", 4)),
                    int(request.form.get("skim_output_fps", 12)),
                    float(request.form.get("preview_seconds", 0)),
                    int(request.form.get("sample_index", 1)),
                    float(request.form.get("source_seconds", 0)),
                    "kiss",
                    "kiss",
                    utc_now_iso(),
                ),
            )
        return redirect(url_for("film_detail", film_id=film_id))

    @app.post("/marks/<int:mark_id>/tag")
    def update_mark_tag(mark_id: int):
        selected_tag = request.form.get("selected_tag", "").strip()
        if not selected_tag:
            abort(400, "A tag must be selected")
        return_film_id = request.form.get("return_film_id", type=int)
        with get_connection(settings.db_path) as conn:
            mark = conn.execute("SELECT * FROM manual_marks WHERE id = ?", (mark_id,)).fetchone()
            if not mark:
                abort(404)
            conn.execute(
                "UPDATE manual_marks SET selected_tag = ?, note = ? WHERE id = ?",
                (selected_tag, selected_tag, mark_id),
            )
            conn.execute(
                """
                UPDATE manual_clips
                SET clip_tag = ?,
                    metadata_json = json_set(COALESCE(NULLIF(metadata_json, ''), '{}'), '$.tag', ?)
                WHERE manual_mark_id = ?
                """,
                (selected_tag, selected_tag, mark_id),
            )
        if return_film_id:
            return redirect(url_for("film_detail", film_id=return_film_id))
        return redirect(url_for("films_index"))

    @app.post("/films/<int:film_id>/marks/<int:mark_id>/build-clip")
    def build_clip(film_id: int, mark_id: int):
        pre_seconds = float(request.form.get("pre_seconds", 20))
        post_seconds = float(request.form.get("post_seconds", 20))
        with get_connection(settings.db_path) as conn:
            mark = conn.execute("SELECT selected_tag FROM manual_marks WHERE id = ? AND film_id = ?", (mark_id, film_id)).fetchone()
            if not mark or not mark["selected_tag"]:
                abort(400, "A tagged mark is required before building a clip")
            conn.execute(
                """
                INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
                VALUES (?, 'build_manual_clip', 'queued', ?, ?, ?, ?)
                """,
                (
                    film_id,
                    json.dumps({"mark_id": mark_id, "pre_seconds": pre_seconds, "post_seconds": post_seconds}, sort_keys=True),
                    json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                    utc_now_iso(),
                    utc_now_iso(),
                ),
            )
            job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        _spawn_pipeline_command(
            settings,
            [
                sys.executable,
                "-m",
                "ia_kissing_pipeline.webapp",
                "build-manual-clip",
                "--job-id",
                str(job_id),
                "--film-id",
                str(film_id),
                "--mark-id",
                str(mark_id),
                "--pre-seconds",
                str(pre_seconds),
                "--post-seconds",
                str(post_seconds),
            ],
        )
        return redirect(url_for("film_detail", film_id=film_id))

    @app.post("/clips/<int:clip_id>/kiss-timing")
    def update_clip_kiss_timing(clip_id: int):
        return_film_id = request.form.get("return_film_id", type=int)
        kiss_start_seconds = request.form.get("kiss_start_seconds", "").strip()
        kiss_end_seconds = request.form.get("kiss_end_seconds", "").strip()
        with get_connection(settings.db_path) as conn:
            _persist_clip_kiss_timing(conn, clip_id, kiss_start_seconds, kiss_end_seconds)
        if return_film_id:
            return redirect(url_for("film_detail", film_id=return_film_id))
        return redirect(url_for("clips_index"))

    @app.post("/films/<int:film_id>/finalize")
    def finalize_review(film_id: int):
        action = request.form.get("action", "").strip()
        notes = request.form.get("review_notes", "").strip() or None
        review_status = "has_kiss" if action == "has_kiss" else "no_kiss"
        clip_timings_json = request.form.get("clip_timings_json", "").strip()
        if clip_timings_json:
            payload = json.loads(clip_timings_json)
            with get_connection(settings.db_path) as conn:
                for item in payload:
                    _persist_clip_kiss_timing(
                        conn,
                        int(item["clip_id"]),
                        item.get("kiss_start_seconds", ""),
                        item.get("kiss_end_seconds", ""),
                    )
        _apply_film_review_action(settings, film_id, review_status, notes)
        return redirect(url_for("index"))

    @app.post("/films/<int:film_id>/force-exclude")
    def force_exclude_route(film_id: int):
        _apply_film_review_action(settings, film_id, "force_excluded", "force excluded from /films")
        return redirect(url_for("films_index"))

    @app.get("/clips")
    def clips_index():
        with get_connection(settings.db_path) as conn:
            clips = _load_clips(
                conn,
                settings.clips_dir,
                film_id=request.args.get("film_id", type=int),
                tag=request.args.get("tag", type=str),
            )
        return render_template_string(CLIPS_TEMPLATE, clips=clips)

    @app.get("/review_data")
    def review_data_index():
        with get_connection(settings.db_path) as conn:
            sections = _load_review_data_sections(conn, settings)
        return render_template_string(REVIEW_DATA_TEMPLATE, sections=sections)

    @app.post("/review_data/delete")
    def delete_review_data_file():
        kind = request.form.get("kind", "").strip()
        relpath = request.form.get("relpath", "").strip()
        path = _resolve_review_data_path(settings, kind, relpath)
        if path is None or not path.exists():
            abort(404)
        resolved_path = path.resolve()
        with get_connection(settings.db_path) as conn:
            if kind == "clip":
                clip = conn.execute(
                    """
                    SELECT id, clip_path, cropped_clip_path
                    FROM manual_clips
                    WHERE clip_path = ? OR cropped_clip_path = ?
                    """,
                    (str(resolved_path), str(resolved_path)),
                ).fetchone()
                if clip:
                    clip_item = dict(clip)
                    for path_value in (clip_item.get("cropped_clip_path"), clip_item.get("clip_path")):
                        if not path_value:
                            continue
                        clip_path = Path(path_value)
                        if clip_path.exists():
                            clip_path.unlink()
                    conn.execute("DELETE FROM manual_clips WHERE id = ?", (clip_item["id"],))
                else:
                    resolved_path.unlink()
            else:
                resolved_path.unlink()
        return redirect(url_for("review_data_index"))

    @app.post("/review_data/requeue")
    def requeue_review_data_movie():
        film_id = request.form.get("film_id", type=int)
        if not film_id:
            abort(400)
        if not _prepare_film_for_requeue(settings, film_id):
            abort(404)
        _queue_build_skim(settings, film_id, sample_every_seconds=4, output_fps=12)
        return redirect(url_for("review_data_index"))

    @app.get("/admin")
    def admin_index():
        requested_count = max(1, min(100, request.args.get("count", default=1, type=int) or 1))
        with get_connection(settings.db_path) as conn:
            queue_job = _load_download_batch_job(conn)
            ready_count = _count_active_pool_films(conn)
        return render_template_string(
            ADMIN_TEMPLATE,
            message=request.args.get("message", type=str),
            requested_count=requested_count,
            queue_job=queue_job,
            ready_count=ready_count,
        )

    @app.post("/admin/get-more-films")
    def admin_start_get_more_vids():
        count = max(1, min(100, request.form.get("count", default=1, type=int) or 1))
        started, job_id = _start_get_more_vids(settings, count)
        if started:
            message = f"Started get more films job {job_id}."
        else:
            message = f"Get more films is already running as job {job_id}."
        return redirect(url_for("admin_index", message=message, count=count))

    @app.get("/api/random-clips")
    def random_clips_api():
        tag = request.args.get("tag", type=str)
        limit = max(1, min(50, request.args.get("limit", default=1, type=int) or 1))
        with get_connection(settings.db_path) as conn:
            clips = _load_random_clips(
                conn,
                settings.clips_dir,
                limit=limit,
                tag=tag,
                mode=_get_clip_order_mode(settings),
            )
        payload = [
            {
                "id": clip["id"],
                "film_id": clip["film_id"],
                "title": clip["title"],
                "tag": clip.get("clip_tag"),
                "kind": clip["kind"],
                "relpath": clip["relpath"],
                "media_url": url_for("media_file", kind=clip["kind"], relpath=clip["relpath"]),
                "start_seconds": clip["start_seconds"],
                "end_seconds": clip["end_seconds"],
                "kiss_start_seconds": clip.get("kiss_start_seconds"),
                "kiss_end_seconds": clip.get("kiss_end_seconds"),
            }
            for clip in clips
        ]
        return jsonify({"clips": payload, "count": len(payload)})

    @app.post("/clips/<int:clip_id>/crop")
    def crop_clip_route(clip_id: int):
        from ia_kissing_pipeline.video.extract_clips import crop_clip

        crop_x = float(request.form.get("crop_x", 0))
        crop_y = float(request.form.get("crop_y", 0))
        crop_width = float(request.form.get("crop_width", 1))
        crop_height = float(request.form.get("crop_height", 1))
        with get_connection(settings.db_path) as conn:
            clip = conn.execute("SELECT * FROM manual_clips WHERE id = ?", (clip_id,)).fetchone()
            if not clip:
                abort(404)
            source_path = Path(clip["clip_path"])
            cropped_path = source_path.with_name(f"{source_path.stem}-crop.mp4")
        crop_clip(source_path, cropped_path, crop_x, crop_y, crop_width, crop_height)
        with get_connection(settings.db_path) as conn:
            conn.execute(
                """
                UPDATE manual_clips
                SET cropped_clip_path = ?, crop_x = ?, crop_y = ?, crop_width = ?, crop_height = ?
                WHERE id = ?
                """,
                (str(cropped_path), crop_x, crop_y, crop_width, crop_height, clip_id),
        )
        return redirect(url_for("clips_index"))

    @app.post("/clips/<int:clip_id>/delete")
    def delete_clip_route(clip_id: int):
        return_film_id = request.form.get("return_film_id", type=int)
        with get_connection(settings.db_path) as conn:
            clip = conn.execute("SELECT * FROM manual_clips WHERE id = ?", (clip_id,)).fetchone()
            if not clip:
                abort(404)
            for path_value in (clip["cropped_clip_path"], clip["clip_path"]):
                if path_value:
                    path = Path(path_value)
                    if path.exists():
                        path.unlink()
            conn.execute("DELETE FROM manual_clips WHERE id = ?", (clip_id,))
        if return_film_id:
            return redirect(url_for("film_detail", film_id=return_film_id))
        return redirect(url_for("clips_index"))

    @app.post("/clips/<int:clip_id>/ignore")
    def toggle_ignore_clip_route(clip_id: int):
        return_film_id = request.form.get("return_film_id", type=int)
        with get_connection(settings.db_path) as conn:
            clip = conn.execute("SELECT ignored FROM manual_clips WHERE id = ?", (clip_id,)).fetchone()
            if not clip:
                abort(404)
            next_value = 0 if int(clip["ignored"] or 0) else 1
            conn.execute("UPDATE manual_clips SET ignored = ? WHERE id = ?", (next_value, clip_id))
        if return_film_id:
            return redirect(url_for("film_detail", film_id=return_film_id))
        return redirect(url_for("clips_index"))

    @app.post("/marks/<int:mark_id>/delete")
    def delete_mark_route(mark_id: int):
        return_film_id = request.form.get("return_film_id", type=int)
        with get_connection(settings.db_path) as conn:
            clip = conn.execute("SELECT * FROM manual_clips WHERE manual_mark_id = ?", (mark_id,)).fetchone()
            if clip:
                for path_value in (clip["cropped_clip_path"], clip["clip_path"]):
                    if path_value:
                        path = Path(path_value)
                        if path.exists():
                            path.unlink()
                conn.execute("DELETE FROM manual_clips WHERE id = ?", (clip["id"],))
            conn.execute("DELETE FROM manual_marks WHERE id = ?", (mark_id,))
        if return_film_id:
            return redirect(url_for("film_detail", film_id=return_film_id))
        return redirect(url_for("films_index"))

    @app.get("/films/<int:film_id>/skim-status")
    def skim_status(film_id: int):
        with get_connection(settings.db_path) as conn:
            job = _load_latest_job(conn, film_id, "build_skim_preview")
        return jsonify(job or {"status": "idle", "progress_percent": 0, "status_text": "idle"})

    @app.get("/films/<int:film_id>/clip-status")
    def clip_status(film_id: int):
        with get_connection(settings.db_path) as conn:
            job = _load_latest_job(conn, film_id, "build_manual_clip")
        return jsonify(job or {"status": "idle", "progress_percent": 0, "status_text": "idle"})

    @app.get("/films/<int:film_id>/skim-overview")
    def skim_overview_payload(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
        if skim is None:
            abort(404)
        frames = _ensure_skim_overview(settings, film["archive_identifier"], skim)
        return jsonify({"frames": frames})

    @app.get("/films/<int:film_id>/kiss-detector")
    def kiss_detector_payload(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
            kiss_detector_job = _load_latest_job(conn, film_id, "kiss_detector")
        if skim is None:
            abort(404)
        return jsonify(_build_kiss_detector_payload(settings, film["archive_identifier"], skim, kiss_detector_job))

    @app.post("/films/<int:film_id>/kiss-detector/analyze")
    def kiss_detector_analyze(film_id: int):
        payload_json = request.get_json(silent=True) or {}
        try:
            job_id = _queue_kiss_detector(
                settings,
                film_id,
                use_workflow_cache=bool(payload_json.get("use_workflow_cache", True)),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
            job = _load_latest_job(conn, film_id, "kiss_detector")
        if not film or skim is None or job is None:
            abort(404)
        payload = _build_kiss_detector_payload(settings, film["archive_identifier"], skim, job)
        payload["job_id"] = job_id
        return jsonify(payload)

    @app.post("/films/<int:film_id>/kiss-detector/stop")
    def kiss_detector_stop(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
        if skim is None:
            abort(404)
        _stop_kiss_detector_job(settings, film_id)
        with get_connection(settings.db_path) as conn:
            job = _load_latest_job(conn, film_id, "kiss_detector")
        return jsonify(_build_kiss_detector_payload(settings, film["archive_identifier"], skim, job))

    @app.post("/films/<int:film_id>/kiss-detector/analyze-collisions")
    def kiss_detector_analyze_collisions(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
            kiss_detector_job = _load_latest_job(conn, film_id, "kiss_detector")
        if skim is None:
            abort(404)
        analyzed = _analyze_kiss_detector_collisions(settings, film["archive_identifier"])
        payload = _build_kiss_detector_payload(settings, film["archive_identifier"], skim, kiss_detector_job)
        payload["collision_analysis_count"] = analyzed
        return jsonify(payload)

    @app.post("/films/<int:film_id>/kiss-detector/cluster")
    def kiss_detector_cluster(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
            kiss_detector_job = _load_latest_job(conn, film_id, "kiss_detector")
        if skim is None:
            abort(404)
        payload_json = request.get_json(silent=True) or {}
        raw_min_size = payload_json.get("min_size_pixels", 0)
        try:
            min_size_pixels = max(0.0, float(raw_min_size))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid min_size_pixels value."}), 400
        analyzed = _cluster_kiss_detector_detections(
            settings,
            film["archive_identifier"],
            min_size_pixels=min_size_pixels,
        )
        payload = _build_kiss_detector_payload(settings, film["archive_identifier"], skim, kiss_detector_job)
        payload["kiss_cluster_analysis_count"] = analyzed
        payload["kiss_cluster_min_size_pixels"] = min_size_pixels
        return jsonify(payload)

    @app.post("/films/<int:film_id>/kiss-detector/make-candidates")
    def kiss_detector_make_candidates(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
            kiss_detector_job = _load_latest_job(conn, film_id, "kiss_detector")
        if skim is None:
            abort(404)
        payload_json = request.get_json(silent=True) or {}
        raw_min_size = payload_json.get("min_size_pixels", payload_json.get("min_area_pixels", 0))
        try:
            min_size_pixels = max(0.0, float(raw_min_size))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid min_size_pixels value."}), 400
        analyzed = _make_kiss_detector_candidates(
            settings,
            film["archive_identifier"],
            min_size_pixels=min_size_pixels,
        )
        payload = _build_kiss_detector_payload(settings, film["archive_identifier"], skim, kiss_detector_job)
        payload["kiss_candidate_analysis_count"] = analyzed
        payload["kiss_candidate_min_size_pixels"] = min_size_pixels
        return jsonify(payload)

    @app.post("/films/<int:film_id>/kiss-detector/remove")
    def kiss_detector_remove(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
        if skim is None:
            abort(404)
        _remove_kiss_detector_outputs(settings, film_id, film["archive_identifier"])
        with get_connection(settings.db_path) as conn:
            job = _load_latest_job(conn, film_id, "kiss_detector")
        return jsonify(_build_kiss_detector_payload(settings, film["archive_identifier"], skim, job))

    @app.get("/films/<int:film_id>/kiss-detector/download-all")
    def kiss_detector_download_all(film_id: int):
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT archive_identifier, title FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                abort(404)
        output_dir = settings.preview_dir / film["archive_identifier"] / "kiss-detector"
        frame_paths = sorted(output_dir.glob("frame_*.png"))
        if not frame_paths:
            abort(404)
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive_file:
            for frame_path in frame_paths:
                archive_file.write(frame_path, arcname=frame_path.name)
        archive_buffer.seek(0)
        safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", film["title"]).strip("-") or film["archive_identifier"]
        return send_file(
            archive_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{safe_stem}-kiss-detector.zip",
        )

    @app.get("/media/<kind>/<path:relpath>")
    def media_file(kind: str, relpath: str):
        roots = {
            "preview": settings.preview_dir,
            "download": settings.download_dir,
            "clip": settings.clips_dir,
        }
        root = roots.get(kind)
        if root is None:
            abort(404)
        path = (root / relpath).resolve()
        if not str(path).startswith(str(root.resolve())) or not path.exists():
            abort(404)
        if kind == "clip":
            with get_connection(settings.db_path) as conn:
                ignored = conn.execute(
                    """
                    SELECT 1
                    FROM manual_clips
                    WHERE ignored = 1
                      AND (
                        clip_path = ?
                        OR cropped_clip_path = ?
                      )
                    LIMIT 1
                    """,
                    (str(path), str(path)),
                ).fetchone()
            if ignored:
                abort(404)
        return send_file(path)

    @app.get("/source")
    def source_archive():
        path = Path("/home/bot/ia-kissing-pipeline-code.zip")
        if not path.exists():
            abort(404)
        return send_file(
            path,
            mimetype="application/zip",
            as_attachment=True,
            download_name="ia-kissing-pipeline-code.zip",
        )

    return app


def _decorate_film_row(film: dict, conn, settings) -> dict:
    review = _get_review_state(conn, film["id"])
    _reconcile_stale_skim_job(conn, film["id"])
    skim = _load_latest_job(conn, film["id"], "build_skim_preview")
    source_cached = _source_cached(settings, film["archive_identifier"])
    film["pipeline_status"] = _display_pipeline_status(film, skim, review, source_cached)
    film_tags = _load_film_tags(conn, film["id"], review)
    clip_counts = _load_film_clip_counts(conn, film["id"])
    active_clip_counts = _load_film_clip_counts(conn, film["id"], include_ignored=False)
    film_tags = [tag for tag in film_tags if clip_counts.get(tag, 0) > 0]
    film["tags"] = film_tags
    film["tags_html"] = " ".join(
        f'<button type="button" class="icon-button film-tag-button {"muted-tag" if active_clip_counts.get(tag, 0) == 0 else ""}" data-film-id="{film["id"]}" data-tag="{tag}" style="margin-right:6px;">{tag} ({clip_counts.get(tag, 0)})</button>'
        for tag in film_tags
    ) or '<span class="small">-</span>'
    film["needs_review"] = film["pipeline_status"] in {
        "checking_metadata",
        "checking_title",
        "awaiting_download",
        "downloading",
        "pending",
    }
    film["show_open_link"] = film["pipeline_status"] in {"pending", "reviewed_has_kiss"}
    film["is_openable"] = film["pipeline_status"] == "pending"
    film["is_dimmed"] = film["pipeline_status"] != "pending"
    return film


def _load_film_tags(conn, film_id: int, review: dict) -> list[str]:
    tags = {
        row["clip_tag"]
        for row in conn.execute(
            "SELECT DISTINCT clip_tag FROM manual_clips WHERE film_id = ? AND clip_tag IS NOT NULL AND clip_tag != ''",
            (film_id,),
        ).fetchall()
    }
    tags.update(
        row["selected_tag"]
        for row in conn.execute(
            "SELECT DISTINCT selected_tag FROM manual_marks WHERE film_id = ? AND selected_tag IS NOT NULL AND selected_tag != ''",
            (film_id,),
        ).fetchall()
    )
    if review["review_status"] == "has_kiss":
        tags.add("kiss")
    return sorted(tags)


def _load_film_clip_counts(conn, film_id: int, include_ignored: bool = True) -> dict[str, int]:
    ignored_clause = "" if include_ignored else "AND ignored = 0"
    return {
        row["clip_tag"]: int(row["count"])
        for row in conn.execute(
            """
            SELECT clip_tag, COUNT(*) AS count
            FROM manual_clips
            WHERE film_id = ? AND clip_tag IS NOT NULL AND clip_tag != ''
              {ignored_clause}
            GROUP BY clip_tag
            """.format(ignored_clause=ignored_clause),
            (film_id,),
        ).fetchall()
    }


def _load_film_rows(settings, filter_tag: str | None = None) -> list[dict]:
    with get_connection(settings.db_path) as conn:
        films = [_decorate_film_row(dict(row), conn, settings) for row in conn.execute("SELECT * FROM films ORDER BY id ASC").fetchall()]
    if filter_tag:
        films = [film for film in films if filter_tag in film["tags"]]
    priority = {
        "pending": 0,
        "awaiting_download": 1,
        "checking_metadata": 2,
        "checking_title": 3,
        "downloading": 4,
        "reviewed_has_kiss": 5,
    }
    films.sort(key=lambda film: (0 if film["needs_review"] else 1, priority.get(film["pipeline_status"], 9), film["id"]))
    return films


def _load_global_tag_stats(settings) -> list[dict]:
    with get_connection(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT clip_tag AS tag, COUNT(*) AS count
            FROM manual_clips
            WHERE clip_tag IS NOT NULL AND clip_tag != ''
            GROUP BY clip_tag
            ORDER BY clip_tag ASC
            """
        ).fetchall()
    return [{"tag": row["tag"], "count": int(row["count"])} for row in rows]


def _get_clip_order_mode(settings) -> str:
    with get_connection(settings.db_path) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'clip_order_mode'").fetchone()
    value = row["value"] if row else "random"
    return value if value in {"random", "ordered"} else "random"


def _set_clip_order_mode(settings, mode: str) -> None:
    with get_connection(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES ('clip_order_mode', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (mode,),
        )


def _display_pipeline_status(film: dict, skim_job: dict | None, review: dict, source_cached: bool) -> str:
    review_status = review["review_status"]
    if review_status != "pending" and not review["cleanup_completed"]:
        return "deleting"
    if review_status == "has_kiss":
        return "reviewed_has_kiss"
    if review_status == "no_kiss":
        return "reviewed_no_kiss"
    if review_status == "force_excluded":
        return "excluded_manual"
    if skim_job and skim_job["status"] in ("queued", "running"):
        return "downloading"
    if skim_job and skim_job["status"] == "error":
        return "source_error"
    status = film["status"]
    if status.startswith("excluded_"):
        return status
    if status == "ingested":
        return "checking_metadata"
    if source_cached and _has_ready_skim(film["id"]):
        return "pending"
    if status in ("metadata_scored", "text_gate_passed", "rights_screened"):
        return "awaiting_download"
    return status


def _get_next_ready_film(conn):
    return conn.execute(
        """
        SELECT f.*
        FROM films f
        LEFT JOIN film_reviews fr ON fr.film_id = f.id
        WHERE COALESCE(fr.review_status, 'pending') = 'pending'
          AND f.status IN ('metadata_scored', 'text_gate_passed', 'rights_screened')
          AND EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status = 'done'
          )
        ORDER BY f.id ASC
        LIMIT 1
        """
    ).fetchone()


def _count_ready_films(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM films f
        LEFT JOIN film_reviews fr ON fr.film_id = f.id
        WHERE COALESCE(fr.review_status, 'pending') = 'pending'
          AND f.status IN ('metadata_scored', 'text_gate_passed', 'rights_screened')
          AND EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status = 'done'
          )
        """
    ).fetchone()
    return int(row["count"])


def _count_active_pool_films(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM films f
        LEFT JOIN film_reviews fr ON fr.film_id = f.id
        WHERE COALESCE(fr.review_status, 'pending') = 'pending'
          AND f.status IN ('metadata_scored', 'text_gate_passed', 'rights_screened')
        """
    ).fetchone()
    return int(row["count"])


def _find_next_download_candidate(conn):
    return conn.execute(
        """
        SELECT f.id
        FROM films f
        LEFT JOIN film_reviews fr ON fr.film_id = f.id
        WHERE f.status IN ('metadata_scored', 'text_gate_passed', 'rights_screened')
          AND COALESCE(fr.review_status, 'pending') = 'pending'
          AND NOT EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status = 'done'
          )
          AND NOT EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status IN ('queued', 'running')
          )
          AND NOT EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status = 'error'
          )
        ORDER BY f.id ASC
        LIMIT 1
        """
    ).fetchone()


def _count_download_candidates(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM films f
        LEFT JOIN film_reviews fr ON fr.film_id = f.id
        WHERE f.status IN ('metadata_scored', 'text_gate_passed', 'rights_screened')
          AND COALESCE(fr.review_status, 'pending') = 'pending'
          AND NOT EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status = 'done'
          )
          AND NOT EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status IN ('queued', 'running')
          )
          AND NOT EXISTS (
            SELECT 1 FROM analysis_jobs j
            WHERE j.film_id = f.id
              AND j.job_type = 'build_skim_preview'
              AND j.status = 'error'
          )
        """
    ).fetchone()
    return int(row["count"])


def _has_ready_skim(film_id: int) -> bool:
    settings = load_settings()
    with get_connection(settings.db_path) as conn:
        return _load_latest_skim(conn, settings.preview_dir, film_id) is not None


def _get_review_state(conn, film_id: int) -> dict:
    row = conn.execute("SELECT review_status, cleanup_completed FROM film_reviews WHERE film_id = ?", (film_id,)).fetchone()
    if not row:
        return {"review_status": "pending", "cleanup_completed": 1}
    return {"review_status": row["review_status"], "cleanup_completed": int(row["cleanup_completed"])}


def _load_latest_skim(conn, preview_dir: Path, film_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT result_json
        FROM analysis_jobs
        WHERE film_id = ? AND job_type = 'build_skim_preview' AND status = 'done'
        ORDER BY id DESC
        LIMIT 1
        """,
        (film_id,),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["result_json"])
    path = Path(payload["preview_path"])
    if not path.exists():
        return None
    return {
        "path": str(path),
        "relpath": str(path.relative_to(preview_dir)),
        "sample_every_seconds": float(payload.get("sample_every_seconds", 4)),
        "output_fps": int(payload.get("output_fps", 12)),
    }


def _ensure_skim_overview(settings, archive_identifier: str, skim: dict) -> list[dict[str, str | int]]:
    frame_paths = _ensure_skim_overview_paths(settings, archive_identifier, skim)
    frames = []
    sample_every_seconds = float(skim.get("sample_every_seconds", 4))
    for index, path in enumerate(frame_paths, start=1):
        relpath = str(path.relative_to(settings.preview_dir))
        frames.append(
            {
                "index": index,
                "source_seconds": int(round((index - 1) * sample_every_seconds)),
                "media_url": url_for("media_file", kind="preview", relpath=relpath),
            }
        )
    return frames


def _ensure_skim_overview_paths(settings, archive_identifier: str, skim: dict) -> list[Path]:
    from ia_kissing_pipeline.video.skim import build_skim_overview_frames

    overview_dir = settings.preview_dir / archive_identifier / "skim-overview"
    return build_skim_overview_frames(Path(skim["path"]), overview_dir)


def _process_kiss_detector_batch(
    settings,
    archive_identifier: str,
    skim: dict,
    *,
    use_workflow_cache: bool = True,
) -> dict[str, object]:
    if not settings.roboflow_api_key:
        raise ValueError("Missing ROBOFLOW_API_KEY in your environment.")
    if not settings.roboflow_workspace_name or not settings.roboflow_workflow_id:
        raise ValueError("Missing ROBOFLOW_WORKSPACE_NAME or ROBOFLOW_WORKFLOW_ID in your environment.")

    frame_paths = _ensure_skim_overview_paths(settings, archive_identifier, skim)
    output_dir = settings.preview_dir / archive_identifier / "kiss-detector"
    output_dir.mkdir(parents=True, exist_ok=True)
    next_missing = None
    for index, frame_path in enumerate(frame_paths, start=1):
        output_path = output_dir / f"frame_{index:06d}.png"
        predictions_path = output_dir / f"frame_{index:06d}.json"
        skipped_path = output_dir / f"frame_{index:06d}.skip"
        if not output_path.exists() and not predictions_path.exists() and not skipped_path.exists():
            next_missing = (index, frame_path, output_path, predictions_path, skipped_path)
            break
    if next_missing is not None:
        _, frame_path, output_path, predictions_path, skipped_path = next_missing
        rendered_bytes, predictions_payload = _run_roboflow_kiss_detector(
            settings,
            frame_path,
            use_workflow_cache=use_workflow_cache,
        )
        _save_workflow_predictions(predictions_payload, predictions_path)
        if rendered_bytes is not None:
            _save_rendered_workflow_image(rendered_bytes, output_path)
        else:
            skipped_path.write_text("no_predictions")
    frames = _list_kiss_detector_outputs(settings, archive_identifier, skim, output_dir)
    total = len(frame_paths)
    skipped = len(list(output_dir.glob("frame_*.skip")))
    completed = len(frames)
    return {
        "frames": frames,
        "completed": completed,
        "total": total,
        "done": completed + skipped >= total,
    }


def _build_kiss_detector_payload(settings, archive_identifier: str, skim: dict, job: dict | None = None) -> dict[str, object]:
    output_dir = settings.preview_dir / archive_identifier / "kiss-detector"
    overview_dir = settings.preview_dir / archive_identifier / "skim-overview"
    frame_paths = sorted(overview_dir.glob("frame_*.jpg"))
    frames = _list_kiss_detector_outputs(settings, archive_identifier, skim, output_dir)
    skipped = len(list(output_dir.glob("frame_*.skip")))
    completed = len(frames)
    total = len(frame_paths)
    payload = {
        "frames": frames,
        "completed": completed,
        "skipped": skipped,
        "total": total,
        "done": total > 0 and completed + skipped >= total,
        "status": "idle",
        "status_text": "Idle",
        "progress_percent": 0,
    }
    if job:
        payload["status"] = job["status"]
        payload["status_text"] = job["status_text"]
        payload["progress_percent"] = job["progress_percent"]
        if job["status"] == "error" and job.get("error_text"):
            message = job["error_text"]
            summary, separator, debug = message.partition("\n\n")
            payload["error"] = summary
            if separator:
                payload["debug"] = debug
    elif total == 0:
        payload["status_text"] = "No saved detector frames yet."
    elif completed or skipped:
        payload["status_text"] = "Saved detector outputs on disk."
    return payload


def _list_kiss_detector_outputs(settings, archive_identifier: str, skim: dict, output_dir: Path | None = None) -> list[dict[str, str | int | bool | None]]:
    output_dir = output_dir or (settings.preview_dir / archive_identifier / "kiss-detector")
    sample_every_seconds = float(skim.get("sample_every_seconds", 4))
    frames = []
    for output_path in sorted(output_dir.glob("frame_*.png")):
        frame_number = int(output_path.stem.split("_")[-1])
        predictions_path = output_path.with_suffix(".json")
        collision = False
        kiss_candidate = False
        if predictions_path.exists():
            try:
                predictions_payload = json.loads(predictions_path.read_text())
                collision = bool(predictions_payload.get("collision", False))
                kiss_candidate = bool(predictions_payload.get("kiss_candidate", False))
            except json.JSONDecodeError:
                collision = False
                kiss_candidate = False
        relpath = str(output_path.relative_to(settings.preview_dir))
        frames.append(
            {
                "index": frame_number,
                "source_seconds": int(round((frame_number - 1) * sample_every_seconds)),
                "media_url": url_for("media_file", kind="preview", relpath=relpath),
                "predictions_url": url_for("media_file", kind="preview", relpath=str(predictions_path.relative_to(settings.preview_dir))) if predictions_path.exists() else None,
                "collision": collision,
                "kiss_candidate": kiss_candidate,
            }
        )
    return frames


def _analyze_kiss_detector_collisions(settings, archive_identifier: str) -> int:
    output_dir = settings.preview_dir / archive_identifier / "kiss-detector"
    analyzed = 0
    for predictions_path in sorted(output_dir.glob("frame_*.json")):
        try:
            predictions_payload = json.loads(predictions_path.read_text())
        except json.JSONDecodeError:
            continue
        predictions = predictions_payload.get("predictions")
        if isinstance(predictions, dict):
            detections = predictions.get("predictions", [])
        else:
            detections = predictions_payload.get("predictions", [])
        collision = _frame_has_polygon_collision(detections if isinstance(detections, list) else [])
        predictions_payload["collision"] = collision
        predictions_path.write_text(json.dumps(predictions_payload, indent=2, sort_keys=True))
        analyzed += 1
    return analyzed


def _make_kiss_detector_candidates(
    settings,
    archive_identifier: str,
    *,
    min_size_pixels: float,
    max_overlap_ratio: float = 0.72,
) -> int:
    output_dir = settings.preview_dir / archive_identifier / "kiss-detector"
    analyzed = 0
    for predictions_path in sorted(output_dir.glob("frame_*.json")):
        try:
            predictions_payload = json.loads(predictions_path.read_text())
        except json.JSONDecodeError:
            continue
        if not bool(predictions_payload.get("collision", False)):
            predictions_payload["kiss_candidate"] = False
            predictions_payload["kiss_candidate_cluster_count"] = 0
            predictions_payload["kiss_candidate_representative_ids"] = []
            predictions_payload["kiss_candidate_min_size_pixels"] = min_size_pixels
            predictions_payload["kiss_candidate_max_overlap_ratio"] = max_overlap_ratio
            predictions_path.write_text(json.dumps(predictions_payload, indent=2, sort_keys=True))
            analyzed += 1
            continue
        detections = _extract_prediction_detections(predictions_payload)
        clusters, cluster_meta = _cluster_frame_detections(
            detections,
            min_size_pixels=min_size_pixels,
            duplicate_overlap_ratio=max_overlap_ratio,
        )
        candidate = _representative_clusters_touch(clusters)
        predictions_payload["kiss_candidate"] = candidate
        predictions_payload["kiss_candidate_min_size_pixels"] = min_size_pixels
        predictions_payload["kiss_candidate_max_overlap_ratio"] = max_overlap_ratio
        predictions_payload["kiss_candidate_cluster_count"] = cluster_meta["cluster_count"]
        predictions_payload["kiss_candidate_representative_ids"] = cluster_meta["representative_ids"]
        predictions_path.write_text(json.dumps(predictions_payload, indent=2, sort_keys=True))
        analyzed += 1
    return analyzed


def _extract_prediction_detections(predictions_payload: dict) -> list[dict]:
    predictions = predictions_payload.get("predictions")
    if isinstance(predictions, dict):
        detections = predictions.get("predictions", [])
    else:
        detections = predictions_payload.get("predictions", [])
    return detections if isinstance(detections, list) else []


def _frame_has_polygon_collision(detections: list[dict]) -> bool:
    polygons = _extract_detection_polygons(detections)
    for index, polygon_a in enumerate(polygons):
        for polygon_b in polygons[index + 1 :]:
            if _polygons_touch_or_overlap(polygon_a["points"], polygon_b["points"]):
                return True
    return False


def _frame_has_kiss_candidate(
    detections: list[dict],
    *,
    min_size_pixels: float,
    max_overlap_ratio: float,
) -> bool:
    clusters, _ = _cluster_frame_detections(
        detections,
        min_size_pixels=min_size_pixels,
        duplicate_overlap_ratio=max_overlap_ratio,
    )
    return _representative_clusters_touch(clusters)


def _cluster_kiss_detector_detections(
    settings,
    archive_identifier: str,
    *,
    min_size_pixels: float,
    duplicate_overlap_ratio: float = 0.72,
) -> int:
    output_dir = settings.preview_dir / archive_identifier / "kiss-detector"
    analyzed = 0
    for predictions_path in sorted(output_dir.glob("frame_*.json")):
        try:
            predictions_payload = json.loads(predictions_path.read_text())
        except json.JSONDecodeError:
            continue
        if not bool(predictions_payload.get("collision", False)):
            predictions_payload["kiss_cluster_min_size_pixels"] = min_size_pixels
            predictions_payload["kiss_cluster_duplicate_overlap_ratio"] = duplicate_overlap_ratio
            predictions_payload["kiss_cluster_count"] = 0
            predictions_payload["kiss_cluster_representative_ids"] = []
            predictions_payload["kiss_cluster_groups"] = []
            predictions_payload["kiss_cluster_irregular_ids"] = []
            predictions_path.write_text(json.dumps(predictions_payload, indent=2, sort_keys=True))
            analyzed += 1
            continue
        detections = _extract_prediction_detections(predictions_payload)
        clusters, cluster_meta = _cluster_frame_detections(
            detections,
            min_size_pixels=min_size_pixels,
            duplicate_overlap_ratio=duplicate_overlap_ratio,
        )
        predictions_payload["kiss_cluster_min_size_pixels"] = min_size_pixels
        predictions_payload["kiss_cluster_duplicate_overlap_ratio"] = duplicate_overlap_ratio
        predictions_payload["kiss_cluster_count"] = cluster_meta["cluster_count"]
        predictions_payload["kiss_cluster_representative_ids"] = cluster_meta["representative_ids"]
        predictions_payload["kiss_cluster_groups"] = cluster_meta["groups"]
        predictions_payload["kiss_cluster_irregular_ids"] = cluster_meta["irregular_ids"]
        source_image_path = predictions_path.with_suffix(".png")
        if source_image_path.exists():
            _write_cluster_overlay(output_dir, predictions_path, clusters)
        predictions_path.write_text(json.dumps(predictions_payload, indent=2, sort_keys=True))
        analyzed += 1
    return analyzed


def _cluster_frame_detections(
    detections: list[dict],
    *,
    min_size_pixels: float,
    duplicate_overlap_ratio: float,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    polygons = [
        polygon
        for polygon in _extract_detection_polygons(detections)
        if polygon["size_pixels"] >= min_size_pixels
    ]
    irregular_polygons = _find_irregular_colliding_polygons(polygons)
    cleaned_polygons = [
        polygon
        for polygon in polygons
        if polygon["shape_id"] not in {irregular_polygon["shape_id"] for irregular_polygon in irregular_polygons}
    ]
    representative_polygons = _cluster_duplicate_detections(
        cleaned_polygons,
        duplicate_overlap_ratio=duplicate_overlap_ratio,
    )
    return representative_polygons, {
        "cluster_count": len(representative_polygons),
        "representative_ids": [polygon.get("detection_id") for polygon in representative_polygons if polygon.get("detection_id")],
        "groups": [
            _ordered_cluster_member_ids(polygon)
            for polygon in representative_polygons
        ],
        "irregular_ids": [polygon["shape_id"] for polygon in irregular_polygons],
        "irregular_polygons": irregular_polygons,
    }


def _find_irregular_colliding_polygons(polygons: list[dict[str, object]]) -> list[dict[str, object]]:
    irregular: list[dict[str, object]] = []
    for index, polygon_a in enumerate(polygons):
        for polygon_b in polygons[index + 1 :]:
            if not _polygons_touch_or_overlap(polygon_a["points"], polygon_b["points"]):
                continue
            if _is_irregular_head_shape(polygon_a) and polygon_a not in irregular:
                irregular.append(polygon_a)
            if _is_irregular_head_shape(polygon_b) and polygon_b not in irregular:
                irregular.append(polygon_b)
    return irregular


def _is_irregular_head_shape(polygon: dict[str, object]) -> bool:
    circularity = float(polygon.get("circularity") or 0.0)
    extent = float(polygon.get("extent") or 0.0)
    aspect_ratio = float(polygon.get("aspect_ratio") or 1.0)
    if aspect_ratio > 4.0:
        return True
    irregular_score = 0
    if circularity < 0.16:
        irregular_score += 1
    if extent < 0.3:
        irregular_score += 1
    if aspect_ratio > 2.6:
        irregular_score += 1
    return irregular_score >= 2


def _representative_clusters_touch(representative_polygons: list[dict[str, object]]) -> bool:
    for index, polygon_a in enumerate(representative_polygons):
        for polygon_b in representative_polygons[index + 1 :]:
            if _polygons_touch_or_overlap(polygon_a["points"], polygon_b["points"]):
                return True
    return False


def _cluster_duplicate_detections(
    polygons: list[dict[str, object]],
    *,
    duplicate_overlap_ratio: float,
    center_distance_ratio: float = 0.35,
) -> list[dict[str, object]]:
    if len(polygons) < 2:
        return polygons
    cluster_indices: list[set[int]] = []
    assigned = [False] * len(polygons)
    for index, polygon in enumerate(polygons):
        if assigned[index]:
            continue
        cluster = {index}
        queue = [index]
        assigned[index] = True
        while queue:
            current_index = queue.pop()
            current = polygons[current_index]
            for candidate_index, candidate in enumerate(polygons):
                if candidate_index in cluster:
                    continue
                if not _detections_are_duplicates(
                    current,
                    candidate,
                    duplicate_overlap_ratio=duplicate_overlap_ratio,
                    center_distance_ratio=center_distance_ratio,
                ):
                    continue
                cluster.add(candidate_index)
                if not assigned[candidate_index]:
                    assigned[candidate_index] = True
                    queue.append(candidate_index)
        cluster_indices.append(cluster)
    return [_select_cluster_representative(polygons, cluster) for cluster in cluster_indices]


def _detections_are_duplicates(
    polygon_a: dict[str, object],
    polygon_b: dict[str, object],
    *,
    duplicate_overlap_ratio: float,
    center_distance_ratio: float,
) -> bool:
    overlap_ratio = _polygon_overlap_ratio(polygon_a["points"], polygon_b["points"])
    if overlap_ratio < duplicate_overlap_ratio:
        return False
    center_a = polygon_a["center"]
    center_b = polygon_b["center"]
    distance = math.dist(center_a, center_b)
    size_limit = min(float(polygon_a["size_pixels"]), float(polygon_b["size_pixels"])) * center_distance_ratio
    return distance <= size_limit


def _select_cluster_representative(polygons: list[dict[str, object]], cluster: set[int]) -> dict[str, object]:
    representative = max(
        (polygons[index] for index in cluster),
        key=lambda polygon: (
            float(polygon.get("confidence") or 0.0),
            float(polygon.get("area") or 0.0),
        ),
    )
    representative = dict(representative)
    representative["cluster_members"] = [dict(polygons[index]) for index in sorted(cluster)]
    return representative


def _write_cluster_overlay(
    output_dir: Path,
    predictions_path: Path,
    representative_polygons: list[dict[str, object]],
) -> None:
    source_image_path = predictions_path.with_suffix(".png")
    image = Image.open(source_image_path).convert("RGBA")
    draw = ImageDraw.Draw(image)
    palette = [
        (255, 99, 71, 255),
        (80, 200, 120, 255),
        (80, 160, 255, 255),
        (255, 215, 0, 255),
        (255, 105, 180, 255),
        (0, 206, 209, 255),
    ]
    for index, representative in enumerate(representative_polygons):
        color = palette[index % len(palette)]
        members = representative.get("cluster_members", [])
        for member in members:
            draw.line(member["points"] + [member["points"][0]], fill=color, width=1)
        draw.line(representative["points"] + [representative["points"][0]], fill=color, width=3)
        center_x, center_y = representative["center"]
        draw.text((center_x + 3, center_y + 3), f"c{index + 1}", fill=color)
    image.save(source_image_path)


def _ordered_cluster_member_ids(representative_polygon: dict[str, object]) -> list[str]:
    representative_id = representative_polygon.get("shape_id")
    members = [
        member.get("shape_id")
        for member in representative_polygon.get("cluster_members", [])
        if member.get("shape_id")
    ]
    if not representative_id or representative_id not in members:
        return members
    return [representative_id] + [member_id for member_id in members if member_id != representative_id]


def _extract_detection_polygons(detections: list[dict]) -> list[dict[str, object]]:
    polygons: list[dict[str, object]] = []
    for index, detection in enumerate(detections, start=1):
        points = detection.get("points")
        if not isinstance(points, list):
            continue
        polygon = []
        for point in points:
            if not isinstance(point, dict):
                continue
            x = point.get("x")
            y = point.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                polygon.append((float(x), float(y)))
        if len(polygon) < 3:
            continue
        bounds = _polygon_bounds(polygon)
        area = _polygon_area(polygon)
        perimeter = _polygon_perimeter(polygon)
        polygons.append(
            {
                "points": polygon,
                "area": area,
                "size_pixels": _detection_size_pixels(detection, polygon),
                "confidence": float(detection.get("confidence") or 0.0),
                "class_name": detection.get("class"),
                "detection_id": detection.get("detection_id"),
                "shape_id": detection.get("detection_id") or f"shape-{index}",
                "center": _detection_center(detection, polygon),
                "bbox": bounds,
                "aspect_ratio": _polygon_aspect_ratio(bounds),
                "extent": _polygon_extent(area, bounds),
                "circularity": _polygon_circularity(area, perimeter),
            }
        )
    return polygons


def _detection_size_pixels(detection: dict, polygon: list[tuple[float, float]]) -> float:
    width = detection.get("width")
    height = detection.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        return max(0.0, min(float(width), float(height)))
    return math.sqrt(max(0.0, _polygon_area(polygon)))


def _detection_center(detection: dict, polygon: list[tuple[float, float]]) -> tuple[float, float]:
    x = detection.get("x")
    y = detection.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return float(x), float(y)
    min_x, min_y, max_x, max_y = _polygon_bounds(polygon)
    return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)


def _polygons_touch_or_overlap(polygon_a: list[tuple[float, float]], polygon_b: list[tuple[float, float]]) -> bool:
    edges_a = list(zip(polygon_a, polygon_a[1:] + polygon_a[:1]))
    edges_b = list(zip(polygon_b, polygon_b[1:] + polygon_b[:1]))
    for edge_a_start, edge_a_end in edges_a:
        for edge_b_start, edge_b_end in edges_b:
            if _segments_touch_or_intersect(edge_a_start, edge_a_end, edge_b_start, edge_b_end):
                return True
    if _point_in_polygon_or_boundary(polygon_a[0], polygon_b):
        return True
    if _point_in_polygon_or_boundary(polygon_b[0], polygon_a):
        return True
    return False


def _polygon_overlap_ratio(polygon_a: list[tuple[float, float]], polygon_b: list[tuple[float, float]]) -> float:
    area_a = _polygon_area(polygon_a)
    area_b = _polygon_area(polygon_b)
    if area_a <= 0 or area_b <= 0:
        return 0.0
    overlap_area = _polygon_overlap_area_estimate(polygon_a, polygon_b)
    return overlap_area / min(area_a, area_b)


def _polygon_overlap_area_estimate(
    polygon_a: list[tuple[float, float]],
    polygon_b: list[tuple[float, float]],
) -> float:
    min_x_a, min_y_a, max_x_a, max_y_a = _polygon_bounds(polygon_a)
    min_x_b, min_y_b, max_x_b, max_y_b = _polygon_bounds(polygon_b)
    min_x = math.floor(max(min_x_a, min_x_b))
    min_y = math.floor(max(min_y_a, min_y_b))
    max_x = math.ceil(min(max_x_a, max_x_b))
    max_y = math.ceil(min(max_y_a, max_y_b))
    if min_x >= max_x or min_y >= max_y:
        return 0.0
    overlap_area = 0.0
    for x in range(min_x, max_x):
        for y in range(min_y, max_y):
            sample_point = (x + 0.5, y + 0.5)
            if _point_in_polygon_or_boundary(sample_point, polygon_a) and _point_in_polygon_or_boundary(sample_point, polygon_b):
                overlap_area += 1.0
    return overlap_area


def _polygon_bounds(polygon: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _polygon_area(polygon: list[tuple[float, float]]) -> float:
    area = 0.0
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _polygon_perimeter(polygon: list[tuple[float, float]]) -> float:
    perimeter = 0.0
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        perimeter += math.dist((x1, y1), (x2, y2))
    return perimeter


def _polygon_aspect_ratio(bounds: tuple[float, float, float, float]) -> float:
    min_x, min_y, max_x, max_y = bounds
    width = max(1e-6, max_x - min_x)
    height = max(1e-6, max_y - min_y)
    return max(width / height, height / width)


def _polygon_extent(area: float, bounds: tuple[float, float, float, float]) -> float:
    min_x, min_y, max_x, max_y = bounds
    bbox_area = max(1e-6, (max_x - min_x) * (max_y - min_y))
    return area / bbox_area


def _polygon_circularity(area: float, perimeter: float) -> float:
    if area <= 0 or perimeter <= 0:
        return 0.0
    return (4.0 * math.pi * area) / (perimeter * perimeter)


def _segments_touch_or_intersect(
    point_a1: tuple[float, float],
    point_a2: tuple[float, float],
    point_b1: tuple[float, float],
    point_b2: tuple[float, float],
) -> bool:
    orientation1 = _orientation(point_a1, point_a2, point_b1)
    orientation2 = _orientation(point_a1, point_a2, point_b2)
    orientation3 = _orientation(point_b1, point_b2, point_a1)
    orientation4 = _orientation(point_b1, point_b2, point_a2)

    if orientation1 != orientation2 and orientation3 != orientation4:
        return True
    if orientation1 == 0 and _point_on_segment(point_b1, point_a1, point_a2):
        return True
    if orientation2 == 0 and _point_on_segment(point_b2, point_a1, point_a2):
        return True
    if orientation3 == 0 and _point_on_segment(point_a1, point_b1, point_b2):
        return True
    if orientation4 == 0 and _point_on_segment(point_a2, point_b1, point_b2):
        return True
    return False


def _orientation(
    point_a: tuple[float, float],
    point_b: tuple[float, float],
    point_c: tuple[float, float],
) -> int:
    value = ((point_b[1] - point_a[1]) * (point_c[0] - point_b[0])) - ((point_b[0] - point_a[0]) * (point_c[1] - point_b[1]))
    if abs(value) < 1e-9:
        return 0
    return 1 if value > 0 else 2


def _point_on_segment(
    point: tuple[float, float],
    segment_start: tuple[float, float],
    segment_end: tuple[float, float],
) -> bool:
    return (
        min(segment_start[0], segment_end[0]) - 1e-9 <= point[0] <= max(segment_start[0], segment_end[0]) + 1e-9
        and min(segment_start[1], segment_end[1]) - 1e-9 <= point[1] <= max(segment_start[1], segment_end[1]) + 1e-9
    )


def _point_in_polygon_or_boundary(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        if _orientation(start, end, point) == 0 and _point_on_segment(point, start, end):
            return True
    inside = False
    x, y = point
    point_count = len(polygon)
    for index in range(point_count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % point_count]
        intersects = ((y1 > y) != (y2 > y)) and (x < ((x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1))
        if intersects:
            inside = not inside
    return inside


def _run_roboflow_kiss_detector(
    settings,
    frame_path: Path,
    *,
    use_workflow_cache: bool = True,
) -> tuple[bytes | None, object]:
    try:
        from inference_sdk import InferenceHTTPClient
    except ImportError as exc:
        raise RuntimeError(
            "Roboflow inference SDK is not installed for this Python version. "
            "Install the `inference` package in the active Python 3.13 environment."
        ) from exc

    client = InferenceHTTPClient(
        api_url=settings.roboflow_api_url,
        api_key=settings.roboflow_api_key,
    )
    try:
        result = client.run_workflow(
            workspace_name=settings.roboflow_workspace_name,
            workflow_id=settings.roboflow_workflow_id,
            images={"image": str(frame_path)},
            parameters={"classes": settings.roboflow_kiss_detector_classes},
            use_cache=use_workflow_cache,
        )
    except Exception as exc:
        raise RuntimeError(f"Roboflow workflow request failed: {exc}") from exc

    predictions_payload = _find_first_workflow_predictions(result)
    if not _workflow_has_predictions(result):
        return None, predictions_payload

    image_payload = _find_first_workflow_image(result)
    if image_payload is None:
        debug_payload = json.dumps(result, indent=2, sort_keys=True, default=str)
        raise RuntimeError(
            "Roboflow workflow response did not contain an image output.\n\n"
            f"{debug_payload}"
        )
    return _decode_workflow_image_payload(image_payload), predictions_payload


def _find_first_workflow_image(node):
    if isinstance(node, dict):
        annotated_image = node.get("annotated_image")
        if isinstance(annotated_image, str) and annotated_image.strip():
            return {"type": "base64", "value": annotated_image}
        for key, value in node.items():
            if not isinstance(value, str) or not value.strip():
                continue
            if key == "label_visualization_output" or key.endswith("_visualization_output"):
                return {"type": "base64", "value": value}
        node_type = node.get("type")
        node_value = node.get("value")
        if node_type in {"base64", "url"} and isinstance(node_value, str):
            return node
        for value in node.values():
            found = _find_first_workflow_image(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_first_workflow_image(item)
            if found is not None:
                return found
    return None


def _find_first_workflow_predictions(node):
    if isinstance(node, dict):
        predictions = node.get("predictions")
        if isinstance(predictions, dict) and "predictions" in predictions:
            return predictions
        for value in node.values():
            found = _find_first_workflow_predictions(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_first_workflow_predictions(item)
            if found is not None:
                return found
    return {"image": {}, "predictions": []}


def _workflow_has_predictions(node) -> bool:
    if isinstance(node, dict):
        predictions = node.get("predictions")
        if isinstance(predictions, list) and len(predictions) > 0:
            return True
        for value in node.values():
            if _workflow_has_predictions(value):
                return True
    elif isinstance(node, list):
        for item in node:
            if _workflow_has_predictions(item):
                return True
    return False


def _decode_workflow_image_payload(payload: dict) -> bytes:
    payload_type = payload.get("type")
    payload_value = payload.get("value", "")
    if payload_type == "url":
        with urllib_request.urlopen(payload_value, timeout=60) as response:
            return response.read()
    if payload_type == "base64":
        value = payload_value
        if value.startswith("data:") and "," in value:
            value = value.split(",", 1)[1]
        return base64.b64decode(value)
    raise RuntimeError(f"Unsupported workflow image payload type: {payload_type}")


def _save_rendered_workflow_image(image_bytes: bytes, output_path: Path) -> None:
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.save(output_path, format="PNG")


def _save_workflow_predictions(predictions_payload, output_path: Path) -> None:
    output_path.write_text(json.dumps(predictions_payload, indent=2, sort_keys=True, default=str))


def _load_latest_job(conn, film_id: int | None, job_type: str) -> dict | None:
    if film_id is None:
        row = conn.execute(
            """
            SELECT id, status, result_json, error_text
            FROM analysis_jobs
            WHERE film_id IS NULL AND job_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_type,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id, status, result_json, error_text
            FROM analysis_jobs
            WHERE film_id = ? AND job_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (film_id, job_type),
        ).fetchone()
    if not row:
        return None
    payload = json.loads(row["result_json"] or "{}")
    progress = float(payload.get("progress", 0.0))
    phase = payload.get("phase", row["status"])
    status_text = {
        "queued": "Queued",
        "expanding_pool": "Adding films to the download pool",
        "extracting_frames": "Extracting frames from source video",
        "numbering_frames": "Numbering sampled frames",
        "encoding_preview": "Encoding skim preview video",
        "detecting_frames": "Analyzing skim frames",
        "building_clip": "Building rough clip",
        "cropping_clip": "Cropping clip",
        "downloading_ready": "Downloading films",
        "done": "Done",
        "error": row["error_text"] or "Error",
    }.get(phase, phase.replace("_", " "))
    return {
        "id": row["id"],
        "status": row["status"],
        "phase": phase,
        "progress": progress,
        "progress_percent": int(max(0, min(100, round(progress * 100)))),
        "status_text": status_text,
        "error_text": row["error_text"],
    }


def _load_marks(conn, clips_dir: Path, film_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT manual_marks.*, manual_clips.id AS clip_id, manual_clips.clip_path, manual_clips.cropped_clip_path, manual_clips.metadata_json, manual_clips.ignored AS clip_ignored
        FROM manual_marks
        LEFT JOIN manual_clips ON manual_clips.manual_mark_id = manual_marks.id
        WHERE manual_marks.film_id = ?
        ORDER BY manual_marks.id DESC
        """,
        (film_id,),
    ).fetchall()
    marks = []
    for row in rows:
        item = dict(row)
        clip_path = item.get("cropped_clip_path") or item.get("clip_path")
        item["clip_relpath"], item["clip_kind"] = _resolve_media_relpath(clip_path, clips_dir)
        metadata = json.loads(item.get("metadata_json") or "{}")
        item["kiss_start_seconds"] = metadata.get("kiss_start_seconds")
        item["kiss_end_seconds"] = metadata.get("kiss_end_seconds")
        item["clip_ignored"] = bool(item.get("clip_ignored"))
        marks.append(item)
    return marks


def _persist_clip_kiss_timing(conn, clip_id: int, kiss_start_seconds, kiss_end_seconds) -> None:
    clip = conn.execute("SELECT metadata_json FROM manual_clips WHERE id = ?", (clip_id,)).fetchone()
    if not clip:
        raise ValueError(f"Clip {clip_id} not found")
    metadata = json.loads(clip["metadata_json"] or "{}")
    metadata["kiss_start_seconds"] = float(kiss_start_seconds) if str(kiss_start_seconds).strip() else None
    metadata["kiss_end_seconds"] = float(kiss_end_seconds) if str(kiss_end_seconds).strip() else None
    conn.execute(
        "UPDATE manual_clips SET metadata_json = ? WHERE id = ?",
        (json.dumps(metadata, sort_keys=True), clip_id),
    )


def _load_clips(conn, clips_dir: Path, film_id: int | None = None, tag: str | None = None) -> list[dict]:
    clauses = ["manual_clips.ignored = 0"]
    params: list[object] = []
    if film_id is not None:
        clauses.append("manual_clips.film_id = ?")
        params.append(film_id)
    if tag:
        clauses.append("manual_clips.clip_tag = ?")
        params.append(tag)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT manual_clips.*, films.title
        FROM manual_clips
        JOIN films ON films.id = manual_clips.film_id
        {where_sql}
        ORDER BY manual_clips.created_at DESC
        """,
        params,
    ).fetchall()
    return _hydrate_clip_rows(rows, clips_dir)


def _load_random_clips(conn, clips_dir: Path, limit: int, tag: str | None = None, mode: str = "random") -> list[dict]:
    clauses = ["manual_clips.ignored = 0"]
    params: list[object] = []
    if tag:
        clauses.append("manual_clips.clip_tag = ?")
        params.append(tag)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    if mode == "ordered":
        cursor = _get_clip_order_cursor(conn, tag)
        base_sql = f"""
            SELECT manual_clips.*, films.title
            FROM manual_clips
            JOIN films ON films.id = manual_clips.film_id
            {where_sql}
        """
        rows = conn.execute(
            f"""
            {base_sql}
            AND manual_clips.id > ?
            ORDER BY manual_clips.id ASC
            LIMIT ?
            """,
            [*params, cursor, limit],
        ).fetchall()
        if len(rows) < limit:
            rows += conn.execute(
                f"""
                {base_sql}
                ORDER BY manual_clips.id ASC
                LIMIT ?
                """,
                [*params, limit - len(rows)],
            ).fetchall()
        clips = _hydrate_clip_rows(rows, clips_dir)
        if clips:
            _set_clip_order_cursor(conn, tag, int(clips[-1]["id"]))
        return clips
    rows = conn.execute(
        f"""
        SELECT manual_clips.*, films.title
        FROM manual_clips
        JOIN films ON films.id = manual_clips.film_id
        {where_sql}
        ORDER BY RANDOM()
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return _hydrate_clip_rows(rows, clips_dir)


def _hydrate_clip_rows(rows, clips_dir: Path) -> list[dict]:
    clips = []
    for row in rows:
        item = dict(row)
        clip_path = item.get("cropped_clip_path") or item.get("clip_path")
        relpath, kind = _resolve_media_relpath(clip_path, clips_dir)
        if not relpath:
            continue
        metadata = json.loads(item.get("metadata_json") or "{}")
        item["kiss_start_seconds"] = metadata.get("kiss_start_seconds")
        item["kiss_end_seconds"] = metadata.get("kiss_end_seconds")
        item["relpath"] = relpath
        item["kind"] = kind
        clips.append(item)
    return clips


def _clip_order_cursor_key(tag: str | None) -> str:
    return f"clip_order_cursor:{tag}" if tag else "clip_order_cursor:all"


def _get_clip_order_cursor(conn, tag: str | None) -> int:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (_clip_order_cursor_key(tag),),
    ).fetchone()
    if not row:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _set_clip_order_cursor(conn, tag: str | None, clip_id: int) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (_clip_order_cursor_key(tag), str(clip_id)),
    )


def _resolve_media_relpath(path_value: str | None, clips_dir: Path) -> tuple[str | None, str]:
    if not path_value:
        return None, "clip"
    path = Path(path_value)
    if not path.exists():
        return None, "clip"
    try:
        return str(path.relative_to(clips_dir)), "clip"
    except ValueError:
        preview_dir = load_settings().preview_dir
        try:
            return str(path.relative_to(preview_dir)), "preview"
        except ValueError:
            return None, "clip"


def _load_download_batch_job(conn) -> dict | None:
    return (
        _load_latest_job(conn, None, "download_batch")
        or _load_latest_job(conn, None, "get_more_vids")
        or _load_latest_job(conn, None, "ensure_review_queue")
    )


def _source_cached(settings, archive_identifier: str) -> bool:
    source_dir = settings.download_dir / archive_identifier
    return source_dir.exists() and any(path.is_file() for path in source_dir.iterdir())


def _format_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size_bytes}B"


def _resolve_review_data_path(settings, kind: str, relpath: str) -> Path | None:
    roots = {
        "download": settings.download_dir,
        "preview": settings.preview_dir,
        "clip": settings.clips_dir,
        "data": settings.db_path.parent,
    }
    root = roots.get(kind)
    if root is None:
        return None
    path = (root / relpath).resolve()
    if not str(path).startswith(str(root.resolve())):
        return None
    return path


def _build_film_status_map(conn, settings) -> dict[int, str]:
    status_map: dict[int, str] = {}
    for row in conn.execute("SELECT * FROM films ORDER BY id ASC").fetchall():
        film = dict(row)
        review = _get_review_state(conn, film["id"])
        skim = _load_latest_job(conn, film["id"], "build_skim_preview")
        source_cached = _source_cached(settings, film["archive_identifier"])
        status_map[int(film["id"])] = _display_pipeline_status(film, skim, review, source_cached)
    return status_map


def _review_data_status(status_map: dict[int, str], film_id: int | None) -> tuple[str, str, str | None]:
    if film_id is None:
        return "stray", "stray", None
    pipeline_status = status_map.get(int(film_id))
    if pipeline_status in {"pending", "awaiting_download", "downloading", "checking_metadata", "checking_title"}:
        return "pending", "pending review", pipeline_status
    if pipeline_status:
        return "linked", f"linked: {pipeline_status}", pipeline_status
    return "stray", "stray", None


def _load_review_data_sections(conn, settings) -> list[dict]:
    status_map = _build_film_status_map(conn, settings)
    clip_rows = conn.execute(
        """
        SELECT id, film_id, clip_path, cropped_clip_path, ignored
        FROM manual_clips
        """
    ).fetchall()
    clip_path_index: dict[str, dict] = {}
    for row in clip_rows:
        item = dict(row)
        for path_value in (item.get("clip_path"), item.get("cropped_clip_path")):
            if path_value:
                clip_path_index[str(Path(path_value).resolve())] = item
    film_by_archive = {
        row["archive_identifier"]: int(row["id"])
        for row in conn.execute("SELECT id, archive_identifier FROM films").fetchall()
    }

    def scan_section(
        title: str,
        kind: str,
        root: Path,
        playable: bool = True,
        include_root_files_only: bool = False,
        open_by_default: bool = False,
    ) -> dict:
        items = []
        files = []
        if include_root_files_only:
            files = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES]
        else:
            files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES]
        for path in sorted(files):
            relpath = str(path.relative_to(root))
            ignored = False
            film_id = None
            if kind == "clip":
                clip_row = clip_path_index.get(str(path.resolve()))
                if clip_row:
                    ignored = bool(clip_row.get("ignored"))
                    film_id = clip_row.get("film_id")
            else:
                parts = Path(relpath).parts
                archive_identifier = parts[0] if len(parts) > 1 else None
                if archive_identifier:
                    film_id = film_by_archive.get(archive_identifier)
            status_kind, status_text, pipeline_status = _review_data_status(status_map, film_id)
            items.append(
                {
                    "kind": kind,
                    "relpath": relpath,
                    "display_path": relpath,
                    "size_text": _format_size(path.stat().st_size),
                    "media_url": url_for("media_file", kind=kind, relpath=relpath) if playable and not ignored else None,
                    "playable": playable and not ignored,
                    "ignored": ignored,
                    "film_id": film_id,
                    "status_kind": status_kind,
                    "status_text": status_text,
                    "can_delete": status_kind == "stray" or pipeline_status == "source_error",
                }
            )
        return {
            "title": title,
            "root": str(root),
            "count": len(items),
            "items": items,
            "open": open_by_default,
        }

    return [
        scan_section("Downloaded Sources", "download", settings.download_dir, open_by_default=True),
        scan_section("Skim Previews", "preview", settings.preview_dir),
        scan_section("Saved Clips", "clip", settings.clips_dir),
        scan_section("Loose Data Videos", "data", settings.db_path.parent, playable=False, include_root_files_only=True),
    ]


def _spawn_pipeline_command(settings, command: list[str]) -> None:
    env = os.environ.copy()
    env.setdefault("DB_PATH", str(settings.db_path))
    env.setdefault("CACHE_DIR", str(settings.cache_dir))
    env.setdefault("DOWNLOAD_DIR", str(settings.download_dir))
    env.setdefault("FRAME_DIR", str(settings.frame_dir))
    env.setdefault("PREVIEW_DIR", str(settings.preview_dir))
    env.setdefault("CLIPS_DIR", str(settings.clips_dir))
    env.setdefault("LOG_DIR", str(settings.log_dir))
    project_root = Path(__file__).resolve().parents[2]
    log_path = settings.log_dir / "webapp-jobs.log"
    with log_path.open("a") as log_file:
        subprocess.Popen(command, cwd=project_root, env=env, stdout=log_file, stderr=log_file, start_new_session=True)


def _queue_kiss_detector(settings, film_id: int, *, use_workflow_cache: bool = True) -> int:
    with get_connection(settings.db_path) as conn:
        film = conn.execute("SELECT id FROM films WHERE id = ?", (film_id,)).fetchone()
        if not film:
            raise ValueError(f"Film {film_id} not found")
        skim = _load_latest_skim(conn, settings.preview_dir, film_id)
        if skim is None:
            raise ValueError("Build a skim preview before running the kiss detector.")
        active_job = _load_latest_job(conn, film_id, "kiss_detector")
        if active_job and active_job["status"] in ("queued", "running"):
            return int(active_job["id"])
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (?, 'kiss_detector', 'queued', ?, ?, ?, ?)
            """,
            (
                film_id,
                json.dumps({"use_workflow_cache": use_workflow_cache}, sort_keys=True),
                json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _spawn_pipeline_command(
        settings,
        [
            sys.executable,
            "-m",
            "ia_kissing_pipeline.webapp",
            "kiss-detector-job",
            "--job-id",
            str(job_id),
            "--film-id",
            str(film_id),
        ],
    )
    return int(job_id)


def _remove_kiss_detector_outputs(settings, film_id: int, archive_identifier: str) -> None:
    output_dir = settings.preview_dir / archive_identifier / "kiss-detector"
    _terminate_film_workers(film_id)
    now = utc_now_iso()
    with get_connection(settings.db_path) as conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'error', error_text = 'kiss detector outputs removed by user', updated_at = ?
            WHERE film_id = ? AND job_type = 'kiss_detector' AND status IN ('queued', 'running')
            """,
            (now, film_id),
        )
    if not output_dir.exists():
        return
    for path in output_dir.glob("frame_*.*"):
        if path.suffix.lower() in {".png", ".json", ".skip"}:
            path.unlink()
    shutil.rmtree(output_dir / "cluster-overlays", ignore_errors=True)


def _stop_kiss_detector_job(settings, film_id: int) -> None:
    _terminate_film_workers(film_id)
    now = utc_now_iso()
    with get_connection(settings.db_path) as conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'error', error_text = 'kiss detector interrupted by user', updated_at = ?
            WHERE film_id = ? AND job_type = 'kiss_detector' AND status IN ('queued', 'running')
            """,
            (now, film_id),
        )


def _queue_build_skim(settings, film_id: int, sample_every_seconds: float = 4, output_fps: int = 12, max_height: int = 360) -> int:
    with get_connection(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (?, 'build_skim_preview', 'queued', ?, ?, ?, ?)
            """,
            (
                film_id,
                json.dumps({"sample_every_seconds": sample_every_seconds, "output_fps": output_fps}, sort_keys=True),
                json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _spawn_pipeline_command(
        settings,
        [
            sys.executable,
            "-m",
            "ia_kissing_pipeline.webapp",
            "build-skim-job",
            "--job-id",
            str(job_id),
            "--film-id",
            str(film_id),
            "--sample-every-seconds",
            str(sample_every_seconds),
            "--output-fps",
            str(output_fps),
            "--max-height",
            str(max_height),
        ],
    )
    return int(job_id)


def _prepare_film_for_requeue(settings, film_id: int) -> bool:
    now = utc_now_iso()
    with get_connection(settings.db_path) as conn:
        film = conn.execute("SELECT id FROM films WHERE id = ?", (film_id,)).fetchone()
        if not film:
            return False
        _terminate_film_workers(film_id)
        conn.execute(
            """
            UPDATE films
            SET status = 'metadata_scored', updated_at = ?
            WHERE id = ?
            """,
            (now, film_id),
        )
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'error', error_text = 'superseded by manual requeue', updated_at = ?
            WHERE film_id = ?
              AND job_type = 'build_skim_preview'
              AND status IN ('queued', 'running')
            """,
            (now, film_id),
        )
        conn.execute(
            """
            INSERT INTO film_reviews (film_id, review_status, review_notes, reviewed_at, cleanup_completed, cleanup_at)
            VALUES (?, 'pending', 'requeued from review_data', NULL, 0, NULL)
            ON CONFLICT(film_id) DO UPDATE SET
                review_status = 'pending',
                review_notes = 'requeued from review_data',
                reviewed_at = NULL,
                cleanup_completed = 0,
                cleanup_at = NULL
            """,
            (film_id,),
        )
    return True


def _build_manual_clip_now(job_id: int, film_id: int, mark_id: int, pre_seconds: float, post_seconds: float) -> int:
    settings = load_settings()
    settings.ensure_directories()
    try:
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "running", "building_clip", 0.35)
            conn.commit()
            film = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
            mark = conn.execute("SELECT * FROM manual_marks WHERE id = ? AND film_id = ?", (mark_id, film_id)).fetchone()
            if not film or not mark:
                _update_job(conn, job_id, "error", "error", 1.0, "Film or mark not found")
                raise SystemExit("Film or mark not found")
            if not mark["selected_tag"]:
                _update_job(conn, job_id, "error", "error", 1.0, "Tagged mark required before building clip")
                raise SystemExit("Tagged mark required before building clip")
            _, _, source_path = _resolve_source_video(conn, settings, film_id, prefer_largest=True)
        from ia_kissing_pipeline.video.extract_clips import extract_clip

        clip_path = settings.clips_dir / film["archive_identifier"] / f"manual-mark-{mark_id:03d}.mp4"
        start_seconds = max(0.0, float(mark["source_seconds"]) - pre_seconds)
        end_seconds = float(mark["source_seconds"]) + post_seconds
        extract_clip(source_path, clip_path, start_seconds, end_seconds - start_seconds)
        with get_connection(settings.db_path) as conn:
            conn.execute("DELETE FROM manual_clips WHERE manual_mark_id = ?", (mark_id,))
            conn.execute(
                """
                INSERT INTO manual_clips (manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mark_id,
                    film_id,
                    str(clip_path),
                    mark["selected_tag"],
                    json.dumps(
                        {
                            "tag": mark["selected_tag"],
                            "mark_preview_seconds": float(mark["preview_seconds"]),
                            "mark_sample_index": int(mark["sample_index"]),
                            "mark_source_seconds": float(mark["source_seconds"]),
                            "mark_note": mark["note"],
                            "kiss_start_seconds": float(pre_seconds) if mark["selected_tag"] == "kiss" else None,
                        },
                        sort_keys=True,
                    ),
                    start_seconds,
                    end_seconds,
                    utc_now_iso(),
                ),
            )
            _update_job(conn, job_id, "done", "done", 1.0)
    except BaseException as exc:
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "error", "error", 1.0, str(exc))
        return 1
    return 0


def _build_skim_now(job_id: int, film_id: int, sample_every_seconds: float, output_fps: int, max_height: int) -> int:
    settings = load_settings()
    settings.ensure_directories()
    try:
        with get_connection(settings.db_path) as conn:
            film, _, source_path = _resolve_source_video(conn, settings, film_id, prefer_largest=False)
            output_path = settings.preview_dir / film["archive_identifier"] / "skim-preview.mp4"

            def progress_callback(phase: str, progress: float) -> None:
                with get_connection(settings.db_path) as callback_conn:
                    _update_job(callback_conn, job_id, "running", phase, progress)

            from ia_kissing_pipeline.video.skim import build_skim_preview

            _update_job(conn, job_id, "running", "queued", 0.1)
            conn.commit()
        build_skim_preview(
            source_path,
            output_path,
            sample_every_seconds=sample_every_seconds,
            output_fps=output_fps,
            max_height=max_height,
            progress_callback=progress_callback,
        )
        with get_connection(settings.db_path) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'done', result_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(
                        {
                            "preview_path": str(output_path),
                            "sample_every_seconds": sample_every_seconds,
                            "output_fps": output_fps,
                            "phase": "done",
                            "progress": 1.0,
                        },
                        sort_keys=True,
                    ),
                    utc_now_iso(),
                    job_id,
                ),
            )
    except BaseException as exc:
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "error", "error", 1.0, str(exc))
        return 1
    return 0


def _run_kiss_detector_now(job_id: int, film_id: int) -> int:
    settings = load_settings()
    settings.ensure_directories()
    try:
        if not settings.roboflow_api_key:
            raise ValueError("Missing ROBOFLOW_API_KEY in your environment.")
        if not settings.roboflow_workspace_name or not settings.roboflow_workflow_id:
            raise ValueError("Missing ROBOFLOW_WORKSPACE_NAME or ROBOFLOW_WORKFLOW_ID in your environment.")
        with get_connection(settings.db_path) as conn:
            film = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
            if not film:
                raise ValueError(f"Film {film_id} not found")
            skim = _load_latest_skim(conn, settings.preview_dir, film_id)
            if skim is None:
                raise ValueError("Build a skim preview before running the kiss detector.")
            job_config_row = conn.execute("SELECT payload_json FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
            job_config = json.loads(job_config_row["payload_json"] or "{}") if job_config_row else {}
            use_workflow_cache = bool(job_config.get("use_workflow_cache", True))
            _update_job(conn, job_id, "running", "queued", 0.05)
            conn.commit()
        frame_paths = _ensure_skim_overview_paths(settings, film["archive_identifier"], skim)
        output_dir = settings.preview_dir / film["archive_identifier"] / "kiss-detector"
        output_dir.mkdir(parents=True, exist_ok=True)
        total = len(frame_paths)
        for index, frame_path in enumerate(frame_paths, start=1):
            output_path = output_dir / f"frame_{index:06d}.png"
            predictions_path = output_dir / f"frame_{index:06d}.json"
            skipped_path = output_dir / f"frame_{index:06d}.skip"
            if not output_path.exists() and not predictions_path.exists() and not skipped_path.exists():
                rendered_bytes, predictions_payload = _run_roboflow_kiss_detector(
                    settings,
                    frame_path,
                    use_workflow_cache=use_workflow_cache,
                )
                _save_workflow_predictions(predictions_payload, predictions_path)
                if rendered_bytes is not None:
                    _save_rendered_workflow_image(rendered_bytes, output_path)
                else:
                    skipped_path.write_text("no_predictions")
            progress = 0.05 + 0.9 * index / max(1, total)
            with get_connection(settings.db_path) as conn:
                _update_job(conn, job_id, "running", "detecting_frames", progress)
        with get_connection(settings.db_path) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'done', result_json = ?, error_text = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps({"phase": "done", "progress": 1.0}, sort_keys=True),
                    utc_now_iso(),
                    job_id,
                ),
            )
    except BaseException as exc:
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "error", "error", 1.0, str(exc))
        return 1
    return 0


def _ensure_review_queue_now(job_id: int, target_ready: int) -> int:
    settings = load_settings()
    settings.ensure_directories()
    try:
        if not _start_queue_runtime(settings, job_id, target_ready):
            return 0
        _terminate_duplicate_queue_workers(os.getpid())
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "running", "filling_queue", 0.05)
        _cleanup_nonpending_local_artifacts(settings)
        ready_count = 0
        ingest_attempts = 0
        while True:
            _heartbeat_queue_runtime(settings, job_id, target_ready)
            with get_connection(settings.db_path) as conn:
                ready_count = _count_active_pool_films(conn)
                candidate = _find_next_download_candidate(conn)
            if candidate:
                with get_connection(settings.db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
                        VALUES (?, 'build_skim_preview', 'queued', ?, ?, ?, ?)
                        """,
                        (
                            candidate["id"],
                            json.dumps({"sample_every_seconds": 4, "output_fps": 12}, sort_keys=True),
                            json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                            utc_now_iso(),
                            utc_now_iso(),
                        ),
                    )
                    skim_job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                _build_skim_now(skim_job_id, candidate["id"], 4, 12, 360)
                continue
            if ready_count >= target_ready:
                break
            if ingest_attempts >= 4:
                break
            if not _ingest_and_score_more(settings):
                break
            _cleanup_nonpending_local_artifacts(settings)
            ingest_attempts += 1
            with get_connection(settings.db_path) as conn:
                ready_count = _count_active_pool_films(conn)
        with get_connection(settings.db_path) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'done', result_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps({"phase": "done", "progress": 1.0, "ready_count": ready_count, "ingest_attempts": ingest_attempts}, sort_keys=True), utc_now_iso(), job_id),
            )
        _finish_queue_runtime(settings, job_id, target_ready, "idle", None)
    except BaseException as exc:
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "error", "error", 1.0, str(exc))
        _finish_queue_runtime(settings, job_id, target_ready, "error", str(exc))
        return 1
    return 0


def _download_more_vids_now(job_id: int, requested_count: int) -> int:
    settings = load_settings()
    settings.ensure_directories()
    completed = 0
    ingest_attempts = 0
    try:
        if not _start_queue_runtime(settings, job_id, requested_count):
            return 0
        _terminate_duplicate_queue_workers(os.getpid())
        with get_connection(settings.db_path) as conn:
            start_pool_count = _count_active_pool_films(conn)
            _update_job(conn, job_id, "running", "expanding_pool", 0.05)
        target_pool_count = start_pool_count + requested_count
        while True:
            _heartbeat_queue_runtime(settings, job_id, requested_count)
            _cleanup_nonpending_local_artifacts(settings)
            with get_connection(settings.db_path) as conn:
                active_pool_count = _count_active_pool_films(conn)
            if active_pool_count >= target_pool_count:
                break
            if ingest_attempts >= 8:
                break
            if not _ingest_and_score_more(settings):
                break
            ingest_attempts += 1
            with get_connection(settings.db_path) as conn:
                active_pool_count = _count_active_pool_films(conn)
                progress = 0.05 if target_pool_count <= start_pool_count else min(
                    0.35,
                    0.05 + 0.30 * max(0, active_pool_count - start_pool_count) / max(1, target_pool_count - start_pool_count),
                )
                _update_job(conn, job_id, "running", "expanding_pool", progress)
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "running", "downloading_ready", 0.35)
            total_candidates = _count_download_candidates(conn)
        while True:
            _heartbeat_queue_runtime(settings, job_id, requested_count)
            _cleanup_nonpending_local_artifacts(settings)
            with get_connection(settings.db_path) as conn:
                candidate = _find_next_download_candidate(conn)
            if not candidate:
                break
            with get_connection(settings.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
                    VALUES (?, 'build_skim_preview', 'queued', ?, ?, ?, ?)
                    """,
                    (
                        candidate["id"],
                        json.dumps({"sample_every_seconds": 4, "output_fps": 12}, sort_keys=True),
                        json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                        utc_now_iso(),
                        utc_now_iso(),
                    ),
                )
                skim_job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            if _build_skim_now(skim_job_id, candidate["id"], 4, 12, 360) == 0:
                completed += 1
                with get_connection(settings.db_path) as conn:
                    _update_job(
                        conn,
                        job_id,
                        "running",
                        "downloading_ready",
                        min(0.95, 0.35 + 0.60 * completed / max(1, total_candidates)),
                    )
        with get_connection(settings.db_path) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'done', result_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(
                        {
                            "phase": "done",
                            "progress": 1.0,
                            "requested_add_count": requested_count,
                            "start_pool_count": start_pool_count,
                            "target_pool_count": target_pool_count,
                            "completed_count": completed,
                            "ingest_attempts": ingest_attempts,
                        },
                        sort_keys=True,
                    ),
                    utc_now_iso(),
                    job_id,
                ),
            )
        _finish_queue_runtime(settings, job_id, requested_count, "idle", None)
    except BaseException as exc:
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "error", "error", 1.0, str(exc))
        _finish_queue_runtime(settings, job_id, requested_count, "error", str(exc))
        return 1
    return 0


def _start_get_more_vids(settings, count: int) -> tuple[bool, int | None]:
    job_id = None
    with get_connection(settings.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _recover_queue_state(conn, settings)
        if _queue_job_is_active(conn):
            runtime = _load_queue_runtime(conn)
            return False, int(runtime["owner_job_id"] or 0) if runtime else None
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (NULL, 'download_batch', 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"count": count}, sort_keys=True),
                json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        _transition_queue_runtime(conn, "queued", job_id, None, count, None)
    _spawn_pipeline_command(
        settings,
        [
            sys.executable,
            "-m",
            "ia_kissing_pipeline.webapp",
            "get-more-vids",
            "--job-id",
            str(job_id),
            "--count",
            str(count),
        ],
    )
    return True, job_id


def _maybe_start_queue_fill(settings, target_ready: int) -> None:
    if os.getenv("IA_KISSING_DISABLE_QUEUE_FILL") == "1":
        return
    job_id = None
    with get_connection(settings.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _recover_queue_state(conn, settings)
        if _queue_job_is_active(conn):
            return
        active_pool_count = _count_active_pool_films(conn)
        if active_pool_count >= target_ready and not _find_next_download_candidate(conn):
            return
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (NULL, 'ensure_review_queue', 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"target_ready": target_ready}, sort_keys=True),
                json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        _transition_queue_runtime(conn, "queued", job_id, None, target_ready, None)
    if job_id is None:
        return
    _spawn_pipeline_command(
        settings,
        [
            sys.executable,
            "-m",
            "ia_kissing_pipeline.webapp",
            "ensure-review-queue",
            "--job-id",
            str(job_id),
            "--target-ready",
            str(target_ready),
        ],
    )


def _ingest_and_score_more(settings) -> bool:
    client = IAClient(settings.cache_dir, settings.user_agent, throttle_seconds=0.2)
    checkpoint_key = make_checkpoint_key(QUEUE_INGEST_QUERY)
    with get_connection(settings.db_path) as conn:
        result = ingest_from_ia(
            conn,
            client,
            query=QUEUE_INGEST_QUERY,
            limit=QUEUE_INGEST_LIMIT,
            rows=QUEUE_INGEST_ROWS,
            checkpoint_key=checkpoint_key,
        )
    run_metadata_scoring(settings)
    # Temporarily disabled in the top-up path. We keep the code and CLI surface,
    # but do not use title screening or rights screening for overnight ingestion.
    return result.films_upserted > 0


def _queue_job_is_active(conn) -> bool:
    row = _load_queue_runtime(conn)
    if not row:
        return False
    return row["state"] in ("queued", "running")


def _reconcile_stale_skim_job(conn, film_id: int) -> None:
    row = conn.execute(
        """
        SELECT id, status
        FROM analysis_jobs
        WHERE film_id = ? AND job_type = 'build_skim_preview'
        ORDER BY id DESC
        LIMIT 1
        """,
        (film_id,),
    ).fetchone()
    if not row or row["status"] not in ("queued", "running"):
        return
    if _skim_worker_active(film_id):
        return
    conn.execute(
        """
        UPDATE analysis_jobs
        SET status = 'error', error_text = COALESCE(error_text, 'stale skim job'), result_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (json.dumps({"phase": "error", "progress": 1.0}, sort_keys=True), utc_now_iso(), row["id"]),
    )


def _skim_worker_active(film_id: int) -> bool:
    result = subprocess.run(
        ["ps", "-eo", "cmd"],
        text=True,
        capture_output=True,
        check=True,
    )
    pattern = re.compile(rf"build-skim-job .*--film-id {film_id}(?:\s|$)")
    return any(pattern.search(line) for line in result.stdout.splitlines())


def _recover_queue_state(conn, settings) -> None:
    runtime = _load_queue_runtime(conn)
    if runtime and runtime["state"] in ("queued", "running"):
        owner_pid = int(runtime["owner_pid"] or 0)
        owner_job_id = int(runtime["owner_job_id"] or 0)
        heartbeat_at = runtime["heartbeat_at"]
        heartbeat_age = _heartbeat_age_seconds(heartbeat_at)
        if runtime["state"] == "queued" and owner_job_id:
            job_row = conn.execute(
                "SELECT status FROM analysis_jobs WHERE id = ? AND job_type IN ('ensure_review_queue', 'get_more_vids', 'download_batch')",
                (owner_job_id,),
            ).fetchone()
            if not job_row or job_row["status"] not in ("queued", "running"):
                _abandon_queue_runtime(conn, owner_job_id, "queued queue job missing")
        elif runtime["state"] == "running":
            if not _pid_is_alive(owner_pid) or heartbeat_age > QUEUE_STALE_SECONDS:
                if owner_pid and _pid_is_alive(owner_pid):
                    try:
                        os.kill(owner_pid, signal.SIGTERM)
                    except OSError:
                        pass
                _abandon_queue_runtime(conn, owner_job_id, "queue worker stale")
    rows = conn.execute(
        """
        SELECT id
        FROM analysis_jobs
        WHERE film_id IS NULL AND job_type IN ('ensure_review_queue', 'get_more_vids', 'download_batch') AND status IN ('queued', 'running')
        ORDER BY id DESC
        """
    ).fetchall()
    runtime_job_id = int(runtime["owner_job_id"] or 0) if runtime else 0
    for row in rows:
        if row["id"] == runtime_job_id:
            continue
        conn.execute(
            "UPDATE analysis_jobs SET status = 'error', error_text = 'superseded queue job', updated_at = ? WHERE id = ?",
            (utc_now_iso(), row["id"]),
        )


def _load_queue_runtime(conn):
    return conn.execute(
        "SELECT * FROM queue_runtime WHERE queue_name = ?",
        (QUEUE_NAME,),
    ).fetchone()


def _transition_queue_runtime(conn, state: str, owner_job_id: int | None, owner_pid: int | None, target_ready: int, last_error: str | None) -> None:
    conn.execute(
        """
        UPDATE queue_runtime
        SET state = ?, owner_job_id = ?, owner_pid = ?, heartbeat_at = ?, target_ready = ?, last_error = ?, updated_at = ?
        WHERE queue_name = ?
        """,
        (state, owner_job_id, owner_pid, utc_now_iso(), target_ready, last_error, utc_now_iso(), QUEUE_NAME),
    )


def _abandon_queue_runtime(conn, job_id: int, reason: str) -> None:
    if job_id:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'error', error_text = ?, updated_at = ?
            WHERE id = ? AND status IN ('queued', 'running')
            """,
            (reason, utc_now_iso(), job_id),
        )
    _transition_queue_runtime(conn, "idle", None, None, 0, reason)


def _start_queue_runtime(settings, job_id: int, target_ready: int) -> bool:
    with get_connection(settings.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _recover_queue_state(conn, settings)
        runtime = _load_queue_runtime(conn)
        if not runtime:
            raise RuntimeError("queue_runtime row missing")
        if int(runtime["owner_job_id"] or 0) != job_id or runtime["state"] != "queued":
            return False
        _transition_queue_runtime(conn, "running", job_id, os.getpid(), target_ready, None)
    return True


def _heartbeat_queue_runtime(settings, job_id: int, target_ready: int) -> None:
    with get_connection(settings.db_path) as conn:
        runtime = _load_queue_runtime(conn)
        if not runtime or int(runtime["owner_job_id"] or 0) != job_id:
            raise RuntimeError("queue runtime ownership lost")
        _transition_queue_runtime(conn, "running", job_id, os.getpid(), target_ready, None)


def _finish_queue_runtime(settings, job_id: int, target_ready: int, terminal_state: str, last_error: str | None) -> None:
    with get_connection(settings.db_path) as conn:
        runtime = _load_queue_runtime(conn)
        if not runtime or int(runtime["owner_job_id"] or 0) != job_id:
            return
        _transition_queue_runtime(conn, terminal_state, job_id, os.getpid(), target_ready, last_error)
        _transition_queue_runtime(conn, "idle", None, None, 0, last_error)


def _heartbeat_age_seconds(heartbeat_at: str | None) -> int:
    if not heartbeat_at:
        return QUEUE_STALE_SECONDS + 1
    try:
        normalized = heartbeat_at.replace("Z", "+00:00")
        heartbeat_epoch = time.mktime(time.strptime(normalized[:19], "%Y-%m-%dT%H:%M:%S"))
        return max(0, int(time.time() - heartbeat_epoch))
    except (ValueError, TypeError):
        return QUEUE_STALE_SECONDS + 1


def _terminate_duplicate_queue_workers(exclude_pid: int) -> None:
    result = subprocess.run(["ps", "-eo", "pid=,cmd="], text=True, capture_output=True, check=True)
    pattern = re.compile(r"\b(ensure-review-queue|get-more-vids)\b")
    for line in result.stdout.splitlines():
        if not pattern.search(line):
            continue
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == exclude_pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _cleanup_nonpending_local_artifacts(settings) -> None:
    with get_connection(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT f.archive_identifier
            FROM films f
            LEFT JOIN film_reviews fr ON fr.film_id = f.id
            WHERE COALESCE(fr.review_status, 'pending') != 'pending'
               OR f.status NOT IN ('metadata_scored', 'text_gate_passed', 'rights_screened')
            """
        ).fetchall()
        for row in rows:
            _cleanup_film_local_artifacts(settings, row["archive_identifier"])


def _preserve_manual_clips(conn, settings, film) -> None:
    rows = conn.execute("SELECT * FROM manual_clips WHERE film_id = ? ORDER BY id ASC", (film["id"],)).fetchall()
    archive_dir = settings.clips_dir / film["archive_identifier"]
    archive_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        clip_path = Path(row["clip_path"])
        if clip_path.exists() and not str(clip_path).startswith(str(settings.clips_dir)):
            target_path = archive_dir / clip_path.name
            shutil.copy2(clip_path, target_path)
            conn.execute("UPDATE manual_clips SET clip_path = ? WHERE id = ?", (str(target_path), row["id"]))
        cropped_path = row["cropped_clip_path"]
        if cropped_path:
            cropped = Path(cropped_path)
            if cropped.exists() and not str(cropped).startswith(str(settings.clips_dir)):
                target_crop = archive_dir / cropped.name
                shutil.copy2(cropped, target_crop)
                conn.execute("UPDATE manual_clips SET cropped_clip_path = ? WHERE id = ?", (str(target_crop), row["id"]))


def _cleanup_film_local_artifacts(settings, archive_identifier: str) -> None:
    for root in (settings.download_dir, settings.frame_dir, settings.preview_dir):
        target = root / archive_identifier
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def _delete_clip_files_for_film(conn, film_id: int) -> None:
    rows = conn.execute("SELECT clip_path, cropped_clip_path FROM manual_clips WHERE film_id = ?", (film_id,)).fetchall()
    for row in rows:
        for path_value in (row["cropped_clip_path"], row["clip_path"]):
            if path_value:
                path = Path(path_value)
                if path.exists():
                    path.unlink()


def _apply_film_review_action(settings, film_id: int, review_status: str, notes: str | None) -> None:
    last_error = None
    film = None
    for attempt in range(5):
        try:
            with get_connection(settings.db_path) as conn:
                film = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
                if not film:
                    raise ValueError(f"Film {film_id} not found")
                if review_status == "has_kiss":
                    _preserve_manual_clips(conn, settings, film)
                if review_status == "force_excluded":
                    _terminate_film_workers(film_id)
                    _delete_clip_files_for_film(conn, film_id)
                    conn.execute(
                        """
                        UPDATE analysis_jobs
                        SET status = 'error', error_text = 'force excluded by reviewer', updated_at = ?
                        WHERE film_id = ? AND job_type IN ('build_skim_preview', 'build_manual_clip', 'kiss_detector') AND status IN ('queued', 'running')
                        """,
                        (utc_now_iso(), film_id),
                    )
                    conn.execute("DELETE FROM manual_clips WHERE film_id = ?", (film_id,))
                    conn.execute("DELETE FROM manual_marks WHERE film_id = ?", (film_id,))
                conn.execute(
                    """
                    INSERT INTO film_reviews (film_id, review_status, review_notes, reviewed_at, cleanup_completed, cleanup_at)
                    VALUES (?, ?, ?, ?, 0, NULL)
                    ON CONFLICT(film_id) DO UPDATE SET
                        review_status = excluded.review_status,
                        review_notes = excluded.review_notes,
                        reviewed_at = excluded.reviewed_at,
                        cleanup_completed = 0,
                        cleanup_at = NULL
                    """,
                    (film_id, review_status, notes, utc_now_iso()),
                )
            break
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.25 * (attempt + 1))
    if film is None and last_error is not None:
        raise last_error
    _cleanup_film_local_artifacts(settings, film["archive_identifier"])
    for attempt in range(5):
        try:
            with get_connection(settings.db_path) as conn:
                conn.execute(
                    "UPDATE film_reviews SET cleanup_completed = 1, cleanup_at = ? WHERE film_id = ?",
                    (utc_now_iso(), film_id),
                )
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.25 * (attempt + 1))


def _terminate_film_workers(film_id: int) -> None:
    try:
        result = subprocess.run(["ps", "-eo", "pid=,command="], text=True, capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return
    pattern = re.compile(rf"\b(build-skim-job|build-manual-clip|kiss-detector-job)\b.*--film-id {film_id}(?:\s|$)")
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _update_job(conn, job_id: int, status: str, phase: str, progress: float, error_text: str | None = None) -> None:
    payload = {"phase": phase, "progress": progress}
    last_error = None
    for attempt in range(5):
        try:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, result_json = ?, error_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, json.dumps(payload, sort_keys=True), error_text, utc_now_iso(), job_id),
            )
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.25 * (attempt + 1))
    if last_error is not None:
        raise last_error


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "build-manual-clip":
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("build-manual-clip")
        parser.add_argument("--job-id", type=int, required=True)
        parser.add_argument("--film-id", type=int, required=True)
        parser.add_argument("--mark-id", type=int, required=True)
        parser.add_argument("--pre-seconds", type=float, required=True)
        parser.add_argument("--post-seconds", type=float, required=True)
        args = parser.parse_args()
        return _build_manual_clip_now(args.job_id, args.film_id, args.mark_id, args.pre_seconds, args.post_seconds)
    if len(sys.argv) > 1 and sys.argv[1] == "build-skim-job":
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("build-skim-job")
        parser.add_argument("--job-id", type=int, required=True)
        parser.add_argument("--film-id", type=int, required=True)
        parser.add_argument("--sample-every-seconds", type=float, required=True)
        parser.add_argument("--output-fps", type=int, required=True)
        parser.add_argument("--max-height", type=int, required=True)
        args = parser.parse_args()
        return _build_skim_now(args.job_id, args.film_id, args.sample_every_seconds, args.output_fps, args.max_height)
    if len(sys.argv) > 1 and sys.argv[1] == "kiss-detector-job":
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("kiss-detector-job")
        parser.add_argument("--job-id", type=int, required=True)
        parser.add_argument("--film-id", type=int, required=True)
        args = parser.parse_args()
        return _run_kiss_detector_now(args.job_id, args.film_id)
    if len(sys.argv) > 1 and sys.argv[1] == "ensure-review-queue":
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("ensure-review-queue")
        parser.add_argument("--job-id", type=int, required=True)
        parser.add_argument("--target-ready", type=int, required=True)
        args = parser.parse_args()
        return _ensure_review_queue_now(args.job_id, args.target_ready)
    if len(sys.argv) > 1 and sys.argv[1] == "get-more-vids":
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("get-more-vids")
        parser.add_argument("--job-id", type=int, required=True)
        parser.add_argument("--count", type=int, required=True)
        args = parser.parse_args()
        return _download_more_vids_now(args.job_id, args.count)
    app = create_app()
    app.run(host="0.0.0.0", port=8000, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
