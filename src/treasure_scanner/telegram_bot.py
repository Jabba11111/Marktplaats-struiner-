from __future__ import annotations

import asyncio
import html

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
)

from .config import Config
from .db import Database
from .models import Evaluation

log = structlog.get_logger(__name__)

PRIORITY_EMOJI = {"high": "🔥", "medium": "💎", "low": "💡"}


def format_alert(ev: Evaluation) -> str:
    l = ev.listing
    emoji = PRIORITY_EMOJI.get(ev.priority, "💎")

    price_str = (f"€{l.price:.0f}" if l.price is not None
                 else f"({l.price_type or 'prijs onbekend'})")

    value_line = ""
    if ev.valuation:
        v = ev.valuation
        if l.price is not None and v.estimated_value > 0:
            value_line = (
                f"\n💰 Waarde ≈ <b>€{v.estimated_value:.0f}</b> "
                f"(n={v.sample_size}, {v.source})"
            )
            margin = ev.deal_margin
            if margin is not None:
                value_line += f" → <b>{margin*100:+.0f}%</b>"

    location_str = f"📍 {html.escape(l.location)}" if l.location else ""

    return (
        f"{emoji} <b>SCORE {ev.score}</b> · {price_str}\n"
        f"<b>{html.escape(l.title[:120])}</b>\n"
        f"{location_str}"
        f"{value_line}\n"
        f'🔎 <i>{html.escape(ev.watcher_name)}</i>\n'
        f"🔗 {l.url}"
    )


def _alert_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👀 Bekeken", callback_data=f"seen:{item_id}"),
            InlineKeyboardButton("🚫 Mute", callback_data=f"mute:{item_id}"),
        ],
    ])


class TelegramNotifier:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self._app: Application | None = None
        self._send_queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        if not self.cfg.telegram_token:
            log.warning("telegram_disabled_no_token")
            return
        self._app = Application.builder().token(self.cfg.telegram_token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_start))
        self._app.add_handler(CommandHandler("stats", self._cmd_stats))
        self._app.add_handler(CommandHandler("mute", self._cmd_mute))
        self._app.add_handler(CommandHandler("unmute", self._cmd_unmute))
        self._app.add_handler(CommandHandler("mutes", self._cmd_mutes))
        self._app.add_handler(CommandHandler("watch", self._cmd_watch))
        self._app.add_handler(CommandHandler("watches", self._cmd_watches))
        self._app.add_handler(CommandHandler("recent", self._cmd_recent))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram_bot_started")

    async def stop(self) -> None:
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                log.warning("telegram_stop_error", error=str(e))

    async def send_alert(self, ev: Evaluation) -> None:
        if not self._app:
            log.info("telegram_alert_skipped_no_app", title=ev.listing.title)
            return
        text = format_alert(ev)
        try:
            if ev.listing.thumbnail_url:
                msg = await self._app.bot.send_photo(
                    chat_id=self.cfg.telegram_chat_id,
                    photo=ev.listing.thumbnail_url,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_alert_keyboard(ev.listing.item_id),
                )
            else:
                msg = await self._app.bot.send_message(
                    chat_id=self.cfg.telegram_chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                    reply_markup=_alert_keyboard(ev.listing.item_id),
                )
            self.db.record_alert(
                ev.listing.item_id, ev.watcher_name, ev.score, msg.message_id,
            )
        except TelegramError as e:
            log.error("telegram_send_failed", error=str(e), title=ev.listing.title)

    # --- auth ---

    def _is_admin(self, update: Update) -> bool:
        if self.cfg.telegram_admin_user_id is None:
            return True
        return (update.effective_user
                and update.effective_user.id == self.cfg.telegram_admin_user_id)

    # --- command handlers ---

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>Treasure Scanner</b> aanwezig.\n\n"
            "Commands:\n"
            "/stats — overzicht laatste 24u\n"
            "/recent [n] — recente alerts\n"
            "/watch <query> — voeg ad-hoc watcher toe\n"
            "/watches — lijst ad-hoc watchers\n"
            "/mute <pattern> — mute woord/merk\n"
            "/unmute <pattern>\n"
            "/mutes — actieve mutes\n"
        )
        await update.message.reply_html(text)

    async def _cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        s = self.db.stats(24)
        s_week = self.db.stats(24 * 7)
        text = (
            f"<b>Stats — laatste 24u</b>\n"
            f"Gescand: {s['scanned']}\n"
            f"Geëvalueerd: {s['evaluated']}\n"
            f"Alerts: {s['alerted']}\n\n"
            f"<b>Laatste 7d</b>\n"
            f"Gescand: {s_week['scanned']} · Alerts: {s_week['alerted']}"
        )
        await update.message.reply_html(text)

    async def _cmd_recent(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        n = 5
        if ctx.args:
            try:
                n = max(1, min(20, int(ctx.args[0])))
            except ValueError:
                pass
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT a.score, a.watcher_name, l.title, l.url, l.price, a.sent_at
                   FROM alerts a JOIN listings l ON l.item_id = a.item_id
                   ORDER BY a.id DESC LIMIT ?""", (n,),
            ).fetchall()
        if not rows:
            await update.message.reply_text("Nog geen alerts.")
            return
        lines = []
        for r in rows:
            price = f"€{r['price']:.0f}" if r['price'] else "?"
            lines.append(
                f"<b>{r['score']}</b> · {price} · "
                f"<a href='{r['url']}'>{html.escape(r['title'][:60])}</a>"
            )
        await update.message.reply_html("\n".join(lines),
                                        disable_web_page_preview=True)

    async def _cmd_mute(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            return
        if not ctx.args:
            await update.message.reply_text("Gebruik: /mute <woord of merk>")
            return
        pattern = " ".join(ctx.args).strip()
        self.db.add_mute(pattern, "text")
        await update.message.reply_text(f"Gemuted: {pattern}")

    async def _cmd_unmute(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            return
        if not ctx.args:
            await update.message.reply_text("Gebruik: /unmute <woord>")
            return
        pattern = " ".join(ctx.args).strip()
        n = self.db.remove_mute(pattern)
        await update.message.reply_text(
            f"Verwijderd: {pattern}" if n else f"Niet gevonden: {pattern}"
        )

    async def _cmd_mutes(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        mutes = self.db.list_mutes()
        if not mutes:
            await update.message.reply_text("Geen mutes actief.")
            return
        await update.message.reply_text(
            "\n".join(f"• {p}" for p, _ in mutes)
        )

    async def _cmd_watch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            return
        if not ctx.args:
            await update.message.reply_text("Gebruik: /watch <zoekterm>")
            return
        q = " ".join(ctx.args).strip()
        self.db.add_adhoc_watch(q, update.effective_user.id if update.effective_user else None)
        await update.message.reply_text(f"Watcher toegevoegd: {q}")

    async def _cmd_watches(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        queries = self.db.list_adhoc_watches()
        if not queries:
            await update.message.reply_text("Geen ad-hoc watchers.")
            return
        await update.message.reply_text(
            "\n".join(f"• {q}" for q in queries)
        )

    async def _on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        await q.answer()
        data = q.data or ""
        if data.startswith("seen:"):
            await q.edit_message_reply_markup(reply_markup=None)
        elif data.startswith("mute:"):
            item_id = data.split(":", 1)[1]
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT title FROM listings WHERE item_id = ?", (item_id,)
                ).fetchone()
            if row:
                # Mute on the first significant word(s) of the title.
                words = [w for w in row["title"].split() if len(w) > 3][:2]
                if words:
                    pattern = " ".join(words)
                    self.db.add_mute(pattern, "text")
                    await q.message.reply_text(f"Gemuted: {pattern}")
            await q.edit_message_reply_markup(reply_markup=None)
