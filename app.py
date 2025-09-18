from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import os
import logging
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def is_valid_youtube_url(url):
    """Validate if the URL is a valid YouTube URL"""
    parsed = urlparse(url)
    youtube_domains = ['youtube.com', 'www.youtube.com', 'youtu.be', 'm.youtube.com']
    return parsed.netloc in youtube_domains

def extract_audio_info(url, format_preference='m4a'):
    """Extract audio download URL and metadata from YouTube video"""
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
            'noplaylist': True,
            'quiet': False,  # Enable logging to debug
            'no_warnings': False,
            'extract_flat': False,
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_client': ['android', 'web']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Extracting info for URL: {url}")
            info = ydl.extract_info(url, download=False)

            # Debug: log all available formats
            formats = info.get('formats', [])
            logger.info(f"Found {len(formats)} formats")

            # Look specifically for audio streams
            audio_url = None

            # Method 1: Use yt-dlp's format selection
            if 'url' in info:
                audio_url = info['url']
                logger.info(f"Found main URL: {audio_url}")

            # Method 2: Manual format selection if main URL is not good
            if not audio_url or 'storyboard' in audio_url or 'jpg' in audio_url:
                # Filter for audio-only formats
                audio_formats = []
                for fmt in formats:
                    acodec = fmt.get('acodec', 'none')
                    vcodec = fmt.get('vcodec', 'none')
                    url_fmt = fmt.get('url', '')

                    # Skip if it's a storyboard or image
                    if 'storyboard' in url_fmt or '.jpg' in url_fmt or '.png' in url_fmt:
                        continue

                    # Audio-only formats (no video)
                    if acodec != 'none' and vcodec == 'none':
                        audio_formats.append(fmt)
                        logger.info(f"Audio format found: {fmt.get('format_id')} - {acodec} - {fmt.get('abr')}kbps")

                if audio_formats:
                    # Sort by audio bitrate, prefer higher quality
                    audio_formats.sort(key=lambda x: x.get('abr', 0) or 0, reverse=True)

                    # Prefer m4a format if available
                    m4a_formats = [f for f in audio_formats if f.get('ext') == 'm4a']
                    if m4a_formats:
                        audio_url = m4a_formats[0].get('url')
                        logger.info(f"Selected m4a format: {m4a_formats[0].get('format_id')}")
                    else:
                        audio_url = audio_formats[0].get('url')
                        logger.info(f"Selected best audio format: {audio_formats[0].get('format_id')}")

            # Method 3: Fallback to any format with audio
            if not audio_url or 'storyboard' in audio_url or 'jpg' in audio_url:
                logger.warning("No pure audio format found, looking for mixed formats...")
                for fmt in formats:
                    url_fmt = fmt.get('url', '')
                    acodec = fmt.get('acodec', 'none')

                    # Skip storyboards and images
                    if 'storyboard' in url_fmt or '.jpg' in url_fmt or '.png' in url_fmt:
                        continue

                    if acodec != 'none':
                        audio_url = url_fmt
                        logger.info(f"Selected mixed format: {fmt.get('format_id')}")
                        break

            if not audio_url:
                raise Exception("No valid audio stream found")

            # Validate the URL doesn't contain image extensions
            if 'storyboard' in audio_url or '.jpg' in audio_url or '.png' in audio_url:
                raise Exception("Only storyboard/image URLs found, no audio streams available")

            logger.info(f"Final audio URL: {audio_url[:100]}...")

            return {
                'audio_url': audio_url,
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'success': True
            }

    except Exception as e:
        logger.error(f"Error extracting audio info: {str(e)}")
        return {
            'error': str(e),
            'success': False
        }

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'yt-dlp audio extraction service',
        'version': '1.0.0'
    })

@app.route('/extract', methods=['GET'])
def extract_audio():
    """Extract audio download URL from YouTube video"""
    try:
        # Get URL parameter
        url = request.args.get('url')
        if not url:
            return jsonify({
                'error': 'Missing required parameter: url',
                'success': False
            }), 400

        # Get format parameter (default to m4a)
        format_preference = request.args.get('format', 'm4a')

        # Validate YouTube URL
        if not is_valid_youtube_url(url):
            return jsonify({
                'error': 'Invalid YouTube URL provided',
                'success': False
            }), 400

        # Extract audio information
        result = extract_audio_info(url, format_preference)

        if result.get('success'):
            return jsonify(result), 200
        else:
            return jsonify(result), 500

    except Exception as e:
        logger.error(f"Unexpected error in /extract endpoint: {str(e)}")
        return jsonify({
            'error': f'Internal server error: {str(e)}',
            'success': False
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)