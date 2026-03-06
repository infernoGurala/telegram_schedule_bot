from datetime import datetime, timedelta, time
import asyncio
import hashlib
import hmac
import json
import os
import re
from typing import Optional

import pytz
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from message_builder import build_message_for_date
from timetable import TIMETABLE

TOKEN = "8388895720:AAFczzuwqyDCcz2-SjAF_6wJQFq0pA-rWJw"
GROUP_ID = -5107345082
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))

IST = pytz.timezone("Asia/Kolkata")


# ---------- DAILY AUTO MESSAGE ----------

async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(IST)
    msg = build_message_for_date(now, "Everyone")

    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=msg,
        parse_mode="HTML"
    )

    print("✅ Daily message sent")


# ---------- COMMANDS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Schedule bot running.")


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    msg = build_message_for_date(datetime.now(IST), name)
    await update.message.reply_text(msg, parse_mode="HTML")


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = datetime.now(IST) + timedelta(days=1)
    day = target.strftime("%A").upper()

    if day not in TIMETABLE:
        await update.message.reply_text("Tomorrow is weekend — no classes.")
        return

    name = update.effective_user.first_name
    msg = build_message_for_date(target, name)
    await update.message.reply_text(msg, parse_mode="HTML")


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📅 Weekly Schedule\n\n"

    for day in ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]:
        text += f"{day}\n"
        for s, t in TIMETABLE[day]["morning"]:
            text += f"• {s} – {t}\n"
        for s, t in TIMETABLE[day]["afternoon"]:
            text += f"• {s} – {t}\n"
        text += "\n"

    await update.message.reply_text(text)


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(update.effective_chat.id))


# ---------- GITHUB WEBHOOK ----------

async def send_notification(message: str):
    await telegram_app.bot.send_message(
        chat_id=GROUP_ID,
        text=message,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
    )


def escape_markdown_v2(text: str) -> str:
    if text is None:
        return ""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", str(text))


def escape_markdown_v2_url(url: str) -> str:
    if not url:
        return ""
    return str(url).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def verify_github_signature(body: bytes, signature_header: str) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def format_push_message(payload: dict) -> str:
    repo_name = payload.get("repository", {}).get("full_name", "unknown")
    ref = payload.get("ref", "")
    branch = ref.split("/")[-1] if ref else "unknown"
    pusher = payload.get("pusher", {}).get("name", "unknown")
    commit = payload.get("head_commit") or (payload.get("commits", [{}])[-1] if payload.get("commits") else {})
    commit_message = commit.get("message", "(no message)").split("\n", 1)[0]
    short_sha = commit.get("id", "")[:7] or "unknown"

    lines = [
        f"🚀 New Push — {escape_markdown_v2(repo_name)}",
        f"├ Branch: {escape_markdown_v2(branch)}",
        f"├ By: {escape_markdown_v2(pusher)}",
        f"└ {escape_markdown_v2(short_sha)}: {escape_markdown_v2(commit_message)}",
    ]

    return "\n".join(lines)


def format_workflow_message(payload: dict) -> Optional[str]:
    workflow_run = payload.get("workflow_run", {})
    repo_name = payload.get("repository", {}).get("full_name", "unknown")
    workflow_name = workflow_run.get("name", "unknown")
    run_url = workflow_run.get("html_url", "")

    status = workflow_run.get("status", "")
    conclusion = workflow_run.get("conclusion")

    # Ignore non-completed updates and completed events without a conclusion.
    if status != "completed" or conclusion is None:
        return None

    if conclusion == "success":
        headline = f"✅ Deploy complete — {escape_markdown_v2(repo_name)}"
    elif conclusion == "failure":
        headline = f"❌ Deploy failed — {escape_markdown_v2(repo_name)}"
    elif conclusion == "cancelled":
        headline = f"🚫 Deploy cancelled — {escape_markdown_v2(repo_name)}"
    else:
        return None

    escaped_url = escape_markdown_v2_url(run_url)
    lines = [
        headline,
        f"├ Workflow: {escape_markdown_v2(workflow_name)}",
    ]

    if escaped_url:
        lines.append(f"└ 🔗 [View Run]({escaped_url})")
    else:
        lines.append("└ 🔗 View Run")

    return "\n".join(lines)


web_app = FastAPI()


@web_app.post("/github-webhook")
async def github_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_github_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = request.headers.get("X-GitHub-Event", "")

    if event == "push":
        await send_notification(format_push_message(payload))
    elif event == "workflow_run":
        message = format_workflow_message(payload)
        if message:
            await send_notification(message)

    return {"ok": True}


# ---------- APP ----------

telegram_app = ApplicationBuilder().token(TOKEN).build()

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("today", today))
telegram_app.add_handler(CommandHandler("tomorrow", tomorrow))
telegram_app.add_handler(CommandHandler("week", week))
telegram_app.add_handler(CommandHandler("id", get_id))


# ---------- DAILY 5 AM IST MON–FRI ----------

telegram_app.job_queue.run_daily(
    send_daily,
    time=time(5, 0, tzinfo=IST),
    days=(0, 1, 2, 3, 4),
)


async def main():
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    print("🤖 Bot polling started")
    print(f"🌐 Webhook server listening on 0.0.0.0:{PORT}")

    server = uvicorn.Server(
        uvicorn.Config(web_app, host="0.0.0.0", port=PORT, log_level="info")
    )

    try:
        await server.serve()
    finally:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
