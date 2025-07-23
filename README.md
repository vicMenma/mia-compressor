# Telegram Media Compressor Bot - Koyeb Deployment Guide

## Overview
This bot has been optimized for deployment on Koyeb, a serverless platform that provides automatic scaling and global deployment.

## Prerequisites
1. A Koyeb account ([koyeb.com](https://www.koyeb.com))
2. Telegram Bot Token from [@BotFather](https://t.me/botfather)
3. Telegram API credentials from [my.telegram.org](https://my.telegram.org)

## Files Structure
```
telegram-bot/
├── main.py              # Main bot code
├── requirements.txt     # Python dependencies
├── Dockerfile          # Docker configuration
├── koyeb.toml          # Koyeb configuration
└── README.md           # This guide
```

## Step-by-Step Deployment

### 1. Get Telegram Credentials
1. **Bot Token**: Message [@BotFather](https://t.me/botfather) on Telegram
   - Send `/newbot`
   - Choose a name and username for your bot
   - Save the bot token (format: `123456789:ABCdef...`)

2. **API Credentials**: Visit [my.telegram.org](https://my.telegram.org)
   - Log in with your phone number
   - Go to "API Development Tools"
   - Create a new application
   - Save your `API_ID` and `API_HASH`

### 2. Deploy to Koyeb

#### Option A: GitHub Integration (Recommended)
1. **Push to GitHub**:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin <your-repo-url>
   git push -u origin main
   ```

2. **Deploy on Koyeb**:
   - Go to [Koyeb Dashboard](https://app.koyeb.com)
   - Click "Create App"
   - Choose "GitHub" as source
   - Select your repository
   - Choose "Dockerfile" as build method

#### Option B: Docker Hub
1. **Build and push Docker image**:
   ```bash
   docker build -t your-username/telegram-bot .
   docker push your-username/telegram-bot
   ```

2. **Deploy on Koyeb**:
   - Choose "Docker" as source
   - Enter your Docker image name

### 3. Configure Environment Variables
In the Koyeb dashboard, set these environment variables:

**Required Variables:**
- `API_ID`: Your Telegram API ID
- `API_HASH`: Your Telegram API Hash  
- `BOT_TOKEN`: Your bot token from BotFather
- `ADMIN_USER_ID`: Your Telegram user ID (get from [@userinfobot](https://t.me/userinfobot))

**Optional Variables (with defaults):**
- `MAX_AUDIO_SIZE`: Maximum audio file size in bytes (default: 104857600 = 100MB)
- `MAX_VIDEO_SIZE`: Maximum video file size in bytes (default: 209715200 = 200MB)
- `MAX_FILES_PER_HOUR`: Hourly rate limit per user (default: 10)
- `MAX_FILES_PER_DAY`: Daily rate limit per user (default: 50)
- `MAX_CONCURRENT_PROCESSES`: Max simultaneous compressions (default: 2)
- `PROCESS_TIMEOUT`: Compression timeout in seconds (default: 600)

### 4. Service Configuration
- **Instance Type**: Start with "Nano" (sufficient for most use cases)
- **Scaling**: Auto-scaling enabled by default
- **Health Check**: Configured at `/health` endpoint
- **Port**: 8000 (automatically configured)

## Key Features for Koyeb

### Performance Optimizations
- **Serverless**: Automatic scaling based on demand
- **Memory Efficient**: Optimized for Koyeb's resource limits
- **Fast Processing**: Limited concurrent processes to prevent timeouts
- **Rate Limiting**: Built-in user rate limiting

### Reliability Features
- **Health Checks**: Automatic health monitoring
- **Error Handling**: Comprehensive error handling and logging
- **Cleanup**: Automatic temporary file cleanup
- **Graceful Shutdown**: Proper shutdown procedures

### Monitoring
- **Logging**: Structured logging for debugging
- **Statistics**: Built-in usage statistics
- **Admin Commands**: Admin-only monitoring commands

## Usage
1. Start your bot: Send `/start` to your bot on Telegram
2. Choose compression mode (Audio/Video)
3. Select quality preset
4. Send media files for compression
5. Download compressed results

## Admin Commands
- `/stats` - View global bot statistics (admin only)
- `/cleanup` - Clean temporary files (admin only)
- `/help` - Show help message

## Quality Presets

### Audio Compression
- **Ultra High**: 128kbps, Stereo, MP3
- **High**: 96kbps, Stereo, MP3
- **Medium**: 64kbps, Stereo, MP3 (recommended)
- **Low**: 32kbps, Mono, MP3
- **Ultra Low**: 16kbps, Mono, MP3

### Video Compression
- **Ultra High**: 720p, 25fps, 1000kbps
- **High**: 480p, 25fps, 700kbps
- **Medium**: 360p, 20fps, 500kbps (recommended)
- **Low**: 270p, 15fps, 300kbps
- **Ultra Low**: 240p, 15fps, 150kbps

## Supported Formats
- **Audio**: MP3, AAC, OGG, FLAC, WAV, M4A
- **Video**: MP4, AVI, MKV, MOV, WMV, WEBM

## Rate Limits
- 10 files per hour per user (configurable)
- 50 files per day per user (configurable)
- Queue system for handling multiple requests

## Troubleshooting

### Common Issues
1. **Bot not responding**: Check environment variables and logs
2. **Compression fails**: Verify FFmpeg availability and file format
3. **Out of memory**: Reduce concurrent processes or upgrade instance type
4. **Timeout errors**: Increase `PROCESS_TIMEOUT` value

### Checking Logs
View logs in Koyeb dashboard:
- Go to your app → Services → Logs
- Look for error messages and processing status

### Health Check
Visit `https://your-app-name.koyeb.app/health` to check bot status.

## Cost Optimization
- Use "Nano" instance for light usage
- Scale up to "Micro" or "Small" for heavy usage
- Monitor usage in Koyeb dashboard
- Implement rate limiting to control costs

## Security Notes
- Never commit credentials to Git
- Use environment variables for all sensitive data
- Regularly rotate your bot token
- Monitor bot usage for abuse

## Support
For issues or questions:
- Check Koyeb documentation
- Review bot logs
- Contact [@kinkum1](https://t.me/kinkum1) for bot-specific issues

## License
This bot is provided as-is for educational and personal use.
