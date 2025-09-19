from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp
import os
import logging
import requests
import io
import subprocess
import tempfile
import shutil
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
        # Enhanced yt-dlp configuration for YouTube's latest requirements
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
            'noplaylist': True,
            'quiet': False,  # Enable logging to debug
            'no_warnings': False,
            'extract_flat': False,
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios', 'android'],  # Simplified, reliable clients
                    'skip': ['dash', 'hls'],
                    'check_formats': None,
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Mobile Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0'
            },
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'skip_unavailable_fragments': True,
        }

        # Try multiple extraction methods as fallbacks
        extraction_methods = [
            ('Primary extraction with ios client', 'ios'),
            ('Fallback with android client', 'android'),
            ('Final fallback with web client', 'web'),
        ]

        for method_name, client in extraction_methods:
            try:
                logger.info(f"Attempting {method_name}")

                # Update client for this attempt
                current_opts = ydl_opts.copy()
                current_opts['extractor_args']['youtube']['player_client'] = [client]

                with yt_dlp.YoutubeDL(current_opts) as ydl:
                    info = ydl.extract_info(url, download=False)

                    # Debug: log all available formats
                    formats = info.get('formats', [])
                    logger.info(f"Found {len(formats)} formats with {client} client")

                    # Look specifically for audio streams
                    audio_url = None

                    # Method 1: Use yt-dlp's format selection
                    if 'url' in info:
                        audio_url = info['url']
                        logger.info(f"Found main URL: {audio_url[:100]}...")

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

                    # Validate the URL doesn't contain image extensions
                    if audio_url and 'storyboard' not in audio_url and '.jpg' not in audio_url and '.png' not in audio_url:
                        logger.info(f"Success with {client} client! Final audio URL: {audio_url[:100]}...")
                        return {
                            'audio_url': audio_url,
                            'title': info.get('title', 'Unknown'),
                            'duration': info.get('duration', 0),
                            'success': True,
                            'extraction_method': client
                        }
                    else:
                        logger.warning(f"{client} client returned storyboard/image URLs only")

            except Exception as method_error:
                logger.warning(f"Method {method_name} failed: {str(method_error)}")
                continue

        # If all methods failed
        raise Exception("All extraction methods failed. YouTube may have updated their protection mechanisms.")

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
        'version': '2.0.0',
        'endpoints': {
            '/extract': 'Extract audio URL from YouTube video',
            '/download': 'Download audio file server-side and stream to client (bypasses 403 errors)',
            '/process': 'Complete pipeline: extract + compress audio for Whisper (<25MB, 64kbps, mono, 16kHz)'
        }
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

@app.route('/download', methods=['GET'])
def download_audio():
    """Download audio file server-side and stream it back to bypass YouTube detection"""
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

        # First, extract the audio URL
        extract_result = extract_audio_info(url, format_preference)

        if not extract_result.get('success'):
            return jsonify(extract_result), 500

        audio_url = extract_result['audio_url']
        title = extract_result.get('title', 'audio')

        # Sanitize filename
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_', '.')).rstrip()
        if not safe_title:
            safe_title = "audio"

        filename = f"{safe_title}.{format_preference}"

        logger.info(f"Downloading audio from: {audio_url[:100]}...")

        # Enhanced headers for downloading from YouTube
        download_headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Mobile Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'identity',  # Disable compression for streaming
            'Connection': 'keep-alive',
            'Referer': 'https://www.youtube.com/',
            'Origin': 'https://www.youtube.com',
            'Sec-Fetch-Dest': 'video',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
            'Range': 'bytes=0-',  # Request range to enable streaming
        }

        # Stream the audio file from YouTube to the client
        def generate():
            try:
                with requests.get(audio_url, headers=download_headers, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
            except Exception as e:
                logger.error(f"Error streaming audio: {str(e)}")
                yield b''  # End stream on error

        # Determine content type
        content_type = 'audio/mp4' if format_preference == 'm4a' else 'audio/webm'

        # Return streaming response
        response = Response(
            stream_with_context(generate()),
            content_type=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Cache-Control': 'no-cache',
                'X-Content-Type-Options': 'nosniff',
                'Accept-Ranges': 'bytes',
            }
        )

        return response

    except Exception as e:
        logger.error(f"Unexpected error in /download endpoint: {str(e)}")
        return jsonify({
            'error': f'Internal server error: {str(e)}',
            'success': False
        }), 500

def compress_audio_for_whisper(input_file, output_file):
    """Compress audio file to be under 25MB for OpenAI Whisper"""
    try:
        # Whisper-optimized settings: 64kbps bitrate, m4a format
        cmd = [
            'ffmpeg',
            '-i', input_file,
            '-c:a', 'aac',           # AAC codec for m4a
            '-b:a', '64k',           # 64kbps bitrate for Whisper
            '-ac', '1',              # Mono channel (reduces size)
            '-ar', '16000',          # 16kHz sample rate (Whisper's preference)
            '-movflags', '+faststart', # Optimize for streaming
            '-y',                    # Overwrite output file
            output_file
        ]

        logger.info(f"Compressing audio with command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr}")

        # Check file size
        file_size = os.path.getsize(output_file)
        logger.info(f"Compressed audio file size: {file_size / (1024*1024):.2f} MB")

        if file_size > 24 * 1024 * 1024:  # 24MB to be safe
            logger.warning(f"File still too large ({file_size / (1024*1024):.2f} MB), trying more aggressive compression")

            # More aggressive compression
            cmd_aggressive = [
                'ffmpeg',
                '-i', input_file,
                '-c:a', 'aac',
                '-b:a', '32k',           # Lower bitrate
                '-ac', '1',              # Mono
                '-ar', '16000',          # 16kHz
                '-movflags', '+faststart',
                '-y',
                output_file
            ]

            result = subprocess.run(cmd_aggressive, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise Exception(f"FFmpeg aggressive compression error: {result.stderr}")

            file_size = os.path.getsize(output_file)
            logger.info(f"Aggressively compressed audio file size: {file_size / (1024*1024):.2f} MB")

        return file_size

    except subprocess.TimeoutExpired:
        raise Exception("Audio compression timed out")
    except Exception as e:
        raise Exception(f"Audio compression failed: {str(e)}")

@app.route('/process', methods=['GET'])
def process_audio():
    """Complete pipeline: extract YouTube audio, compress for Whisper (<25MB), and stream binary"""
    temp_dir = None
    try:
        # Get URL parameter
        url = request.args.get('url')
        if not url:
            return jsonify({
                'error': 'Missing required parameter: url',
                'success': False
            }), 400

        # Validate YouTube URL
        if not is_valid_youtube_url(url):
            return jsonify({
                'error': 'Invalid YouTube URL provided',
                'success': False
            }), 400

        logger.info(f"Processing audio for Whisper optimization: {url}")

        # Extract audio information
        try:
            extract_result = extract_audio_info(url, 'm4a')
            if not extract_result.get('success'):
                return jsonify(extract_result), 500

            audio_url = extract_result['audio_url']
            title = extract_result.get('title', 'audio')

            logger.info(f"Successfully extracted audio URL, now downloading and compressing...")

        except Exception as e:
            logger.error(f"Failed to extract audio info: {str(e)}")
            return jsonify({
                'error': f'Failed to extract audio: {str(e)}',
                'success': False
            }), 500

        # Create temporary directory for processing
        temp_dir = tempfile.mkdtemp()
        input_file = os.path.join(temp_dir, 'input.m4a')
        output_file = os.path.join(temp_dir, 'output.m4a')

        # Download audio file
        try:
            logger.info(f"Downloading audio from: {audio_url[:100]}...")

            download_headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Mobile Safari/537.36',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Connection': 'keep-alive',
                'Referer': 'https://www.youtube.com/',
                'Origin': 'https://www.youtube.com',
            }

            with requests.get(audio_url, headers=download_headers, stream=True, timeout=120) as response:
                response.raise_for_status()

                original_size = 0
                with open(input_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            original_size += len(chunk)

                logger.info(f"Downloaded {original_size / (1024*1024):.2f} MB original audio")

        except Exception as e:
            logger.error(f"Failed to download audio: {str(e)}")
            return jsonify({
                'error': f'Failed to download audio: {str(e)}',
                'success': False
            }), 500

        # Compress audio for Whisper
        try:
            compressed_size = compress_audio_for_whisper(input_file, output_file)

            if compressed_size > 25 * 1024 * 1024:
                return jsonify({
                    'error': f'Audio file too large for Whisper: {compressed_size / (1024*1024):.2f} MB (max 25MB)',
                    'success': False
                }), 413

            logger.info(f"Successfully compressed audio to {compressed_size / (1024*1024):.2f} MB for Whisper")

        except Exception as e:
            logger.error(f"Failed to compress audio: {str(e)}")
            return jsonify({
                'error': f'Failed to compress audio: {str(e)}',
                'success': False
            }), 500

        # Stream the compressed audio file
        def generate_compressed_stream():
            try:
                with open(output_file, 'rb') as f:
                    while True:
                        chunk = f.read(16384)  # 16KB chunks
                        if not chunk:
                            break
                        yield chunk
            except Exception as e:
                logger.error(f"Error streaming compressed audio: {str(e)}")
                yield b''
            finally:
                # Cleanup temp directory
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)

        # Create response with Whisper-optimized audio
        response = Response(
            stream_with_context(generate_compressed_stream()),
            content_type='audio/mp4',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0',
                'X-Content-Type-Options': 'nosniff',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET',
                'Access-Control-Allow-Headers': 'Content-Type',
                'X-Audio-Compression': 'whisper-optimized',
                'X-Audio-Bitrate': '64kbps',
                'X-Audio-Format': 'm4a-mono-16khz',
            }
        )

        return response

    except Exception as e:
        logger.error(f"Unexpected error in /process endpoint: {str(e)}")
        # Cleanup on error
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        return jsonify({
            'error': f'Internal server error: {str(e)}',
            'success': False
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)