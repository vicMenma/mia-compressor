# Webhook-Based Telegram Media Compressor Bot for Koyeb
# Fixed for serverless deployment

import os
import tempfile
import subprocess
import asyncio
import json
import logging
from datetime import datetime, timedelta
from aiohttp import web, ClientSession
from pyrogram import Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message, Update
from pyrogram.errors import FloodWait, RPCError
import hashlib
import hmac
from typing import Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
class Config:
    # Bot Credentials
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    # Webhook settings for Koyeb
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Your Koyeb app URL
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}" if BOT_TOKEN else "/webhook"
    PORT = int(os.getenv("PORT"))
    
    # File limits
    MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500MB
    MAX_VIDEO_SIZE = 900 * 1024 * 1024  # 900MB
    
    @classmethod
    def validate(cls):
        if not all([cls.API_ID, cls.API_HASH, cls.BOT_TOKEN]):
            raise ValueError("Missing required environment variables")
        if not cls.WEBHOOK_URL:
            logger.warning("WEBHOOK_URL not set - bot may not work on Koyeb")

# Validate config
try:
    Config.validate()
    logger.info("✅ Configuration validated")
except ValueError as e:
    logger.error(f"❌ Configuration error: {e}")
    exit(1)

# Create Pyrogram client
app = Client(
    "webhook_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workdir="/tmp"
)

# Global state
user_states: Dict[int, Dict] = {}
bot_info = None

def get_user_state(user_id: int) -> Dict:
    if user_id not in user_states:
        user_states[user_id] = {
            "mode": None,
            "last_activity": datetime.now().timestamp()
        }
    return user_states[user_id]

def format_file_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

async def send_message(chat_id: int, text: str, reply_markup=None):
    """Send message using Telegram Bot API directly"""
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    
    async with ClientSession() as session:
        try:
            async with session.post(url, json=payload) as response:
                result = await response.json()
                if result.get("ok"):
                    logger.info(f"✅ Message sent to {chat_id}")
                    return result
                else:
                    logger.error(f"❌ Failed to send message: {result}")
                    return None
        except Exception as e:
            logger.error(f"❌ Error sending message: {e}")
            return None

async def handle_start_command(user_id: int, username: str):
    """Handle /start command"""
    logger.info(f"🚀 START command from user {user_id} (@{username})")
    
    # Reset user state
    user_states.pop(user_id, None)
    
    # Create inline keyboard
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎧 Audio Compression", "callback_data": "mode_audio"}],
            [{"text": "🎥 Video Compression", "callback_data": "mode_video"}],
            [
                {"text": "📊 My Stats", "callback_data": "user_stats"},
                {"text": "ℹ️ Help", "callback_data": "help"}
            ]
        ]
    }
    
    welcome_msg = (
        "🤖 **Media Compressor Bot (Koyeb)**\n\n"
        "⚡ **Fast serverless compression!**\n\n"
        "🎯 **Features:**\n"
        "• High-quality audio & video compression\n"
        "• Multiple quality presets\n"
        "• Smart optimization\n"
        "• Fast serverless processing\n\n"
        "📏 **Limits:**\n"
        f"• Audio: {format_file_size(Config.MAX_AUDIO_SIZE)}\n"
        f"• Video: {format_file_size(Config.MAX_VIDEO_SIZE)}\n\n"
        "Choose your compression mode:"
    )
    
    await send_message(user_id, welcome_msg, keyboard)

async def handle_help_command(user_id: int):
    """Handle /help command"""
    help_text = (
        "ℹ️ **Media Compressor Bot Help**\n\n"
        "📋 **Commands:**\n"
        "• /start - Main menu\n"
        "• /help - Show this help\n"
        "• /test - Test bot response\n\n"
        "🎯 **How to Use:**\n"
        "1. Use /start to choose compression mode\n"
        "2. Select quality preset\n"
        "3. Send your audio/video file\n"
        "4. Wait for processing\n"
        "5. Download compressed result\n\n"
        "⚡ **Powered by Koyeb**"
    )
    
    await send_message(user_id, help_text)

async def handle_test_command(user_id: int):
    """Handle /test command"""
    test_msg = (
        "✅ **Test Successful!**\n\n"
        f"User ID: `{user_id}`\n"
        f"Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
        f"Bot Status: **Online**\n\n"
        "The webhook bot is working correctly!"
    )
    
    await send_message(user_id, test_msg)

async def handle_callback_query(callback_query):
    """Handle callback queries"""
    user_id = callback_query["from"]["id"]
    data = callback_query["data"]
    
    logger.info(f"📱 Callback: {data} from user {user_id}")
    
    user_state = get_user_state(user_id)
    
    if data.startswith("mode_"):
        mode = data.replace("mode_", "")
        user_state["mode"] = mode
        
        if mode == "audio":
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔊 High Quality", "callback_data": "audio_high"}],
                    [{"text": "🔉 Medium Quality ✓", "callback_data": "audio_medium"}],
                    [{"text": "🔈 Low Quality", "callback_data": "audio_low"}],
                    [{"text": "🔙 Back", "callback_data": "back_main"}]
                ]
            }
            
            response_msg = (
                "🎧 **Audio Compression Mode**\n\n"
                "📁 Send audio files or voice messages\n"
                f"📏 Max size: {format_file_size(Config.MAX_AUDIO_SIZE)}\n\n"
                "⚙️ **Quality Presets:**\n"
                "🔊 **High**: 96kbps, Stereo\n"
                "🔉 **Medium**: 64kbps, Stereo (Default)\n"
                "🔈 **Low**: 32kbps, Mono\n\n"
                "Choose quality level:"
            )
        
        elif mode == "video":
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📺 High Quality", "callback_data": "video_high"}],
                    [{"text": "🖥️ Medium Quality ✓", "callback_data": "video_medium"}],
                    [{"text": "📱 Low Quality", "callback_data": "video_low"}],
                    [{"text": "🔙 Back", "callback_data": "back_main"}]
                ]
            }
            
            response_msg = (
                "🎥 **Video Compression Mode**\n\n"
                "📁 Send video files to compress\n"
                f"📏 Max size: {format_file_size(Config.MAX_VIDEO_SIZE)}\n\n"
                "⚙️ **Quality Presets:**\n"
                "📺 **High**: 480p, 25fps\n"
                "🖥️ **Medium**: 360p, 20fps\n"
                "📱 **Low**: 270p, 15fps\n\n"
                "Choose quality level:"
            )
        
        await send_message(user_id, response_msg, keyboard)
    
    elif data.startswith(("audio_", "video_")):
        preset_type, quality = data.split("_", 1)
        user_state[f"{preset_type}_preset"] = quality
        
        response_msg = (
            f"✅ **{preset_type.title()} quality set to: {quality.title()}**\n\n"
            "⚡ **Ready for processing!**\n"
            f"Now send me {preset_type} files to compress!"
        )
        
        await send_message(user_id, response_msg)
    
    elif data == "help":
        await handle_help_command(user_id)
    
    elif data == "user_stats":
        stats_msg = (
            "📊 **Your Statistics**\n\n"
            "📁 Files processed: 0\n"
            "💾 Space saved: 0 B\n\n"
            "⚡ **Powered by Koyeb**\n"
            "Start compressing files to see your stats!"
        )
        await send_message(user_id, stats_msg)
    
    elif data == "back_main":
        username = callback_query["from"].get("username", "Unknown")
        await handle_start_command(user_id, username)
    
    # Answer callback query
    answer_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/answerCallbackQuery"
    async with ClientSession() as session:
        await session.post(answer_url, json={
            "callback_query_id": callback_query["id"],
            "text": "✅"
        })

async def process_update(update_data):
    """Process incoming update"""
    try:
        logger.info(f"📨 Processing update: {json.dumps(update_data, indent=2)}")
        
        if "message" in update_data:
            message = update_data["message"]
            
            if "from" not in message:
                logger.warning("Message has no 'from' field")
                return
            
            user_id = message["from"]["id"]
            username = message["from"].get("username", "Unknown")
            
            # Handle text messages
            if "text" in message:
                text = message["text"]
                logger.info(f"📝 Text message: '{text}' from user {user_id}")
                
                if text == "/start":
                    await handle_start_command(user_id, username)
                elif text == "/help":
                    await handle_help_command(user_id)
                elif text == "/test":
                    await handle_test_command(user_id)
                else:
                    # Unknown command
                    await send_message(user_id, 
                        "❓ Unknown command. Use /start to begin or /help for assistance.")
            
            # Handle media files
            elif any(key in message for key in ["audio", "voice", "video", "document"]):
                user_state = get_user_state(user_id)
                
                if not user_state.get("mode"):
                    await send_message(user_id,
                        "❌ **Please select a compression mode first!**\n"
                        "Use /start to choose Audio or Video compression.")
                else:
                    # For now, just acknowledge receipt
                    await send_message(user_id,
                        "📁 **File received!**\n\n"
                        "Processing functionality will be added soon.\n"
                        "The webhook bot is working correctly!")
        
        elif "callback_query" in update_data:
            await handle_callback_query(update_data["callback_query"])
        
    except Exception as e:
        logger.error(f"❌ Error processing update: {e}")
        import traceback
        traceback.print_exc()

# Webhook handler
async def webhook_handler(request):
    """Handle incoming webhooks from Telegram"""
    try:
        # Get the update data
        update_data = await request.json()
        
        # Process the update
        await process_update(update_data)
        
        return web.Response(text="OK")
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return web.Response(text="Error", status=500)

# Health check
async def health_check(request):
    """Health check endpoint"""
    return web.json_response({
        "status": "healthy",
        "bot_running": True,
        "webhook_configured": bool(Config.WEBHOOK_URL),
        "timestamp": datetime.now().isoformat()
    })

async def setup_webhook():
    """Set up webhook with Telegram"""
    if not Config.WEBHOOK_URL:
        logger.warning("⚠️ WEBHOOK_URL not configured")
        return False
    
    # Fix: Properly construct the webhook URL
    # Make sure WEBHOOK_URL doesn't end with a slash and add the path
    webhook_url = f"{Config.WEBHOOK_URL.rstrip('/')}{Config.WEBHOOK_PATH}"
    
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/setWebhook"
    payload = {"url": webhook_url}
    
    async with ClientSession() as session:
        try:
            async with session.post(url, json=payload) as response:
                result = await response.json()
                if result.get("ok"):
                    logger.info(f"✅ Webhook set to: {webhook_url}")
                    return True
                else:
                    logger.error(f"❌ Failed to set webhook: {result}")
                    return False
        except Exception as e:
            logger.error(f"❌ Error setting webhook: {e}")
            return False
            
async def get_bot_info():
    """Get bot information"""
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/getMe"
    
    async with ClientSession() as session:
        try:
            async with session.get(url) as response:
                result = await response.json()
                if result.get("ok"):
                    bot_data = result["result"]
                    logger.info(f"✅ Bot info: @{bot_data['username']} ({bot_data['first_name']})")
                    return bot_data
                else:
                    logger.error(f"❌ Failed to get bot info: {result}")
                    return None
        except Exception as e:
            logger.error(f"❌ Error getting bot info: {e}")
            return None

async def create_app():
    """Create the web application"""
    app = web.Application()
    
    # Add routes
    app.router.add_post(Config.WEBHOOK_PATH, webhook_handler)
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    return app

async def main():
    """Main function"""
    logger.info("🚀 Webhook Bot Starting...")
    logger.info("=" * 60)
    logger.info(f"🌐 Port: {Config.PORT}")
    logger.info(f"🔗 Webhook Path: {Config.WEBHOOK_PATH}")
    logger.info(f"📡 Webhook URL: {Config.WEBHOOK_URL}")
    logger.info("=" * 60)
    
    # Get bot info
    global bot_info
    bot_info = await get_bot_info()
    if not bot_info:
        logger.error("❌ Failed to get bot info")
        return
    
    # Set up webhook
    webhook_success = await setup_webhook()
    if not webhook_success:
        logger.error("❌ Failed to set up webhook")
        # Continue anyway for local testing
    
    # Create and start web server
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
    
    logger.info("✅ Webhook bot is running!")
    logger.info(f"📱 Bot: @{bot_info['username']}")
    logger.info("🔄 Waiting for updates...")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("🔴 Bot stopped by user")
    finally:
        logger.info("🧹 Shutting down...")
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
