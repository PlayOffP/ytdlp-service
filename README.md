# YT-DLP Audio Extraction Service

A simple web service that extracts audio download URLs from YouTube videos using yt-dlp, designed for use in n8n workflows.

## API Endpoints

### GET /extract

Extracts audio download URL from a YouTube video.

**Parameters:**
- `url` (required): YouTube video URL
- `format` (optional): Audio format preference (default: m4a)

**Example Request:**
```
GET /extract?url=https://www.youtube.com/watch?v=VIDEO_ID&format=m4a
```

**Success Response:**
```json
{
  "audio_url": "https://direct-download-link.m4a",
  "title": "Video Title",
  "duration": 1234,
  "success": true
}
```

**Error Response:**
```json
{
  "error": "Error message",
  "success": false
}
```

### GET /

Health check endpoint.

## Deployment

This service is designed to be deployed on Railway or similar platforms.

### Railway Deployment

1. Connect your GitHub repository to Railway
2. Railway will automatically detect the Python app and deploy using the provided configuration

### Local Development

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the service:
   ```bash
   python app.py
   ```

The service will be available at `http://localhost:5000`

## Usage in n8n

Use the HTTP Request node with:
- Method: GET
- URL: `https://your-app.railway.app/extract?url=YOUTUBE_URL&format=m4a`

The response will contain the direct audio download URL that can be used in subsequent n8n nodes.