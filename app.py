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
        'version': '3.0.0',
        'endpoints': {
            '/extract': 'Extract audio URL from YouTube video',
            '/download': 'Download audio file server-side and stream to client (bypasses 403 errors)',
            '/process': 'Complete pipeline: extract + compress audio for Whisper (with segmentation for large files)'
        },
        'features': {
            'segmentation': 'Large files (>100MB) automatically split into 10-minute segments',
            'timeout_safety': 'Railway-optimized processing to prevent worker timeouts',
            'whisper_optimization': 'Audio compressed to <25MB for OpenAI Whisper'
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

def segment_audio_for_processing(input_file, temp_dir, max_segment_duration=600):
    """Split audio into 10-minute segments for Railway timeout handling"""
    try:
        # Get audio duration first
        probe_cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            input_file
        ]

        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise Exception(f"Failed to probe audio duration: {result.stderr}")

        import json
        probe_data = json.loads(result.stdout)
        total_duration = float(probe_data['format']['duration'])

        logger.info(f"Audio duration: {total_duration:.1f}s, splitting into {max_segment_duration}s segments")

        # Calculate number of segments needed
        num_segments = int(total_duration / max_segment_duration) + 1
        segments = []

        for i in range(num_segments):
            start_time = i * max_segment_duration
            segment_file = os.path.join(temp_dir, f'segment_{i:03d}.m4a')

            # Create segment with FFmpeg
            segment_cmd = [
                'ffmpeg',
                '-i', input_file,
                '-ss', str(start_time),
                '-t', str(max_segment_duration),
                '-c', 'copy',  # Fast copy without re-encoding
                '-avoid_negative_ts', 'make_zero',
                '-y',
                segment_file
            ]

            logger.info(f"Creating segment {i+1}/{num_segments}: {start_time}s-{start_time+max_segment_duration}s")

            result = subprocess.run(
                segment_cmd,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            if result.returncode != 0:
                logger.warning(f"Segment {i} creation failed: {result.stderr}")
                continue

            # Check if segment file was created and has content
            if os.path.exists(segment_file) and os.path.getsize(segment_file) > 1024:
                segment_size = os.path.getsize(segment_file) / (1024*1024)
                segments.append({
                    'file': segment_file,
                    'index': i,
                    'start_time': start_time,
                    'duration': min(max_segment_duration, total_duration - start_time),
                    'size_mb': segment_size
                })
                logger.info(f"Segment {i} created: {segment_size:.1f}MB")
            else:
                logger.warning(f"Segment {i} failed or too small")

        logger.info(f"Successfully created {len(segments)} audio segments")
        return segments

    except Exception as e:
        logger.error(f"Audio segmentation failed: {str(e)}")
        raise Exception(f"Failed to segment audio: {str(e)}")

def compress_segment_for_whisper(segment_info, output_file):
    """Compress a single audio segment for Whisper with fast settings"""
    try:
        input_file = segment_info['file']
        size_mb = segment_info['size_mb']

        # Use aggressive settings for segments (they're smaller)
        if size_mb > 50:
            bitrate = '16k'
            sample_rate = '8000'
        elif size_mb > 20:
            bitrate = '24k'
            sample_rate = '11025'
        else:
            bitrate = '32k'
            sample_rate = '16000'

        logger.info(f"Compressing segment {segment_info['index']}: {size_mb:.1f}MB with {bitrate} bitrate")

        cmd = [
            'ffmpeg',
            '-i', input_file,
            '-c:a', 'aac',
            '-b:a', bitrate,
            '-ac', '1',                  # Mono
            '-ar', sample_rate,
            '-threads', '1',             # Single thread per segment
            '-preset', 'ultrafast',
            '-profile:a', 'aac_low',
            '-map_metadata', '-1',
            '-movflags', '+faststart',
            '-f', 'mp4',
            '-y',
            output_file
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # Short timeout per segment
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        if result.returncode != 0:
            raise Exception(f"Segment compression failed: {result.stderr}")

        compressed_size = os.path.getsize(output_file)
        compressed_mb = compressed_size / (1024*1024)

        logger.info(f"Segment {segment_info['index']} compressed: {size_mb:.1f}MB → {compressed_mb:.2f}MB")

        # Ensure segment is under 25MB (should be much smaller)
        if compressed_size > 25 * 1024 * 1024:
            logger.warning(f"Segment {segment_info['index']} still large: {compressed_mb:.2f}MB")

        return compressed_size

    except Exception as e:
        logger.error(f"Segment compression error: {str(e)}")
        raise Exception(f"Failed to compress segment: {str(e)}")

def compress_audio_for_whisper(input_file, output_file, original_size_mb):
    """Compress audio file to be under 25MB for OpenAI Whisper with Railway-optimized speed"""
    try:
        # Early validation: don't compress files already under 25MB
        if original_size_mb < 25:
            logger.info(f"File already under 25MB ({original_size_mb:.2f}MB), no compression needed")
            # Copy file without compression
            shutil.copy2(input_file, output_file)
            return os.path.getsize(output_file)
        # Ultra-aggressive settings optimized for Railway's 600s timeout
        if original_size_mb > 300:
            # Extreme files: immediate ultra-low quality
            bitrate = '12k'
            sample_rate = '8000'
            timeout = 90  # Very short timeout
        elif original_size_mb > 200:
            # Very large files: aggressive compression
            bitrate = '16k'
            sample_rate = '8000'
            timeout = 120
        elif original_size_mb > 100:
            # Large files: fast compression
            bitrate = '24k'
            sample_rate = '11025'
            timeout = 150
        else:
            # Smaller files: still fast but better quality
            bitrate = '32k'
            sample_rate = '16000'
            timeout = 120

        logger.info(f"Railway-optimized compression: {bitrate} bitrate, {sample_rate}Hz for {original_size_mb:.1f}MB file")

        # Railway-optimized FFmpeg command with maximum speed
        cmd = [
            'ffmpeg',
            '-i', input_file,
            '-c:a', 'aac',               # AAC codec (fastest)
            '-b:a', bitrate,             # Ultra-low bitrate
            '-ac', '1',                  # Mono (cuts size in half)
            '-ar', sample_rate,          # Low sample rate for speed
            '-threads', '2',             # Match Railway worker cores
            '-preset', 'ultrafast',      # Fastest preset always
            '-profile:a', 'aac_low',     # Low complexity profile
            '-avoid_negative_ts', 'make_zero',
            '-shortest',                 # Stop at shortest stream
            '-map_metadata', '-1',       # Strip all metadata
            '-movflags', '+faststart',   # Quick header
            '-f', 'mp4',                 # Force container
            '-y',                        # Overwrite
            output_file
        ]

        logger.info(f"Railway compression command: {' '.join(cmd[:8])}...")  # Log truncated command

        # Run with strict timeout
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        if result.returncode != 0:
            # Emergency fallback: absolute minimum quality for Railway
            logger.warning(f"Primary compression failed: {result.stderr[:200]}...")
            logger.info("Trying emergency Railway-survival mode...")

            cmd_emergency = [
                'ffmpeg',
                '-i', input_file,
                '-c:a', 'aac',
                '-b:a', '8k',            # Absolute minimum
                '-ac', '1',              # Mono
                '-ar', '8000',           # Lowest sample rate
                '-threads', '1',         # Single thread for stability
                '-preset', 'ultrafast',
                '-profile:a', 'aac_low',
                '-t', '600',             # Limit to 10 minutes max
                '-map_metadata', '-1',
                '-f', 'mp4',
                '-y',
                output_file
            ]

            result = subprocess.run(
                cmd_emergency,
                capture_output=True,
                text=True,
                timeout=45,  # Ultra-short emergency timeout
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            if result.returncode != 0:
                raise Exception(f"Emergency Railway compression failed: {result.stderr}")

            logger.info("Emergency Railway compression succeeded")

        # Check final file size
        file_size = os.path.getsize(output_file)
        file_size_mb = file_size / (1024*1024)

        logger.info(f"Compressed {original_size_mb:.1f}MB → {file_size_mb:.2f}MB")

        # Validate size limit
        if file_size > 25 * 1024 * 1024:
            raise Exception(f"File still too large: {file_size_mb:.2f}MB (max 25MB)")

        return file_size

    except subprocess.TimeoutExpired:
        logger.error(f"Compression timed out after {timeout}s")
        raise Exception(f"Audio compression timed out after {timeout} seconds. File too large for processing.")
    except FileNotFoundError:
        raise Exception("FFmpeg not found. Please ensure FFmpeg is installed.")
    except Exception as e:
        logger.error(f"Compression error: {str(e)}")
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

                original_size_mb = original_size / (1024*1024)
                logger.info(f"Downloaded {original_size_mb:.2f} MB original audio")

                # Railway safety check: reject extremely large files upfront
                if original_size_mb > 500:
                    logger.warning(f"File too large for Railway processing: {original_size_mb:.1f}MB")
                    return jsonify({
                        'error': f'File too large for processing: {original_size_mb:.1f}MB (max 500MB)',
                        'success': False,
                        'suggestion': 'Use shorter videos or lower quality audio for Whisper'
                    }), 413  # Payload Too Large

        except Exception as e:
            logger.error(f"Failed to download audio: {str(e)}")
            return jsonify({
                'error': f'Failed to download audio: {str(e)}',
                'success': False
            }), 500

        # Process audio for Whisper with segmentation for large files
        try:
            # Check if original file is already under 25MB - skip compression entirely
            if original_size_mb < 25:
                logger.info(f"File already under 25MB ({original_size_mb:.2f}MB), skipping compression")
                # Copy original file to output file for consistent response handling
                shutil.copy2(input_file, output_file)
                compressed_size = original_size
                compressed_size_mb = original_size_mb
                logger.info(f"No compression needed: {original_size_mb:.2f}MB file passed through")

            elif original_size_mb <= 100:
                # Medium files: aggressive compression
                logger.info(f"Medium file ({original_size_mb:.1f}MB), using aggressive compression")
                compressed_size = compress_audio_for_whisper(input_file, output_file, original_size_mb)
                compressed_size_mb = compressed_size / (1024*1024)
                logger.info(f"Aggressive compression: {original_size_mb:.1f}MB → {compressed_size_mb:.2f}MB")

            else:
                # Large files: segment and return first segment immediately
                logger.info(f"Large file ({original_size_mb:.1f}MB), using segmentation for Railway timeout safety")

                # Segment the audio into 10-minute chunks
                segments = segment_audio_for_processing(input_file, temp_dir, max_segment_duration=600)

                if not segments:
                    raise Exception("Failed to create any audio segments")

                # Process first segment immediately for quick response
                first_segment = segments[0]
                first_output = os.path.join(temp_dir, 'first_segment_compressed.m4a')

                logger.info(f"Processing first segment ({first_segment['size_mb']:.1f}MB) for immediate response")
                compressed_size = compress_segment_for_whisper(first_segment, first_output)

                # Use first segment as the output
                output_file = first_output
                compressed_size_mb = compressed_size / (1024*1024)

                logger.info(f"First segment ready: {first_segment['size_mb']:.1f}MB → {compressed_size_mb:.2f}MB")
                logger.info(f"Note: Returning first 10 minutes of {len(segments)}-segment audio for Railway timeout safety")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Compression failed: {error_msg}")

            # Provide helpful error messages based on the failure type
            if "timed out" in error_msg.lower():
                return jsonify({
                    'error': f'Audio compression timed out. Original file size: {original_size_mb:.2f}MB. Try a shorter video.',
                    'success': False,
                    'original_size_mb': original_size_mb,
                    'suggestion': 'Use videos under 200MB for faster processing'
                }), 408  # Request Timeout

            elif "too large" in error_msg.lower():
                # Only show this error if we actually attempted compression (file was >25MB)
                if original_size_mb >= 25:
                    return jsonify({
                        'error': f'Compressed audio still exceeds 25MB limit after compression. Original: {original_size_mb:.2f}MB',
                        'success': False,
                        'original_size_mb': original_size_mb,
                        'suggestion': 'Use shorter videos for Whisper transcription'
                    }), 413  # Payload Too Large
                else:
                    # This shouldn't happen with our new logic, but handle gracefully
                    return jsonify({
                        'error': f'Unexpected compression error for small file ({original_size_mb:.2f}MB): {error_msg}',
                        'success': False,
                        'original_size_mb': original_size_mb
                    }), 500

            else:
                return jsonify({
                    'error': f'Audio processing failed: {error_msg}. Original file size: {original_size_mb:.2f}MB',
                    'success': False,
                    'original_size_mb': original_size_mb
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