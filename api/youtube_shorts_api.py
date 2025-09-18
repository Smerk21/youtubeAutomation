from flask import Flask, request, jsonify
from flask_cors import CORS
from pytube import YouTube
import os
import time
import logging
from moviepy.editor import VideoFileClip
import urllib.request
import threading
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import json
from werkzeug.utils import secure_filename

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for iOS app

# Configuration
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max file size

# Create necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROCESSED_FOLDER'], exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("youtube_shorts_api.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class YouTubeShortsProcessor:
    def __init__(self):
        self.youtube_service = None
        
    def download_video(self, url, progress_callback=None):
        """Download video from the given URL"""
        try:
            logger.info(f"Downloading video from: {url}")
            
            if progress_callback:
                progress_callback(10, "Starting download...")
            
            # Check if URL is from YouTube
            if "youtube.com" in url or "youtu.be" in url:
                yt = YouTube(url)
                stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
                
                if not stream:
                    logger.error("No suitable stream found")
                    return None
                
                if progress_callback:
                    progress_callback(30, "Downloading video...")
                    
                # Download the video
                output_path = stream.download(output_path=app.config['UPLOAD_FOLDER'])
                logger.info(f"Video downloaded to: {output_path}")
                
                if progress_callback:
                    progress_callback(100, "Download complete!")
                    
                return output_path
            else:
                # For non-YouTube URLs
                if progress_callback:
                    progress_callback(50, "Downloading from URL...")
                    
                video_name = os.path.join(app.config['UPLOAD_FOLDER'], f"video_{int(time.time())}.mp4")
                urllib.request.urlretrieve(url, video_name)
                logger.info(f"Video downloaded to: {video_name}")
                
                if progress_callback:
                    progress_callback(100, "Download complete!")
                    
                return video_name
                
        except Exception as e:
            logger.error(f"Error downloading video: {str(e)}")
            if progress_callback:
                progress_callback(0, f"Error: {str(e)}")
            return None
            
    def convert_to_shorts_format(self, video_path, progress_callback=None):
        """Convert video to YouTube Shorts format (9:16 aspect ratio, 60 seconds or less)"""
        try:
            logger.info(f"Converting video to Shorts format: {video_path}")
            
            if progress_callback:
                progress_callback(10, "Loading video...")
            
            # Load the video
            clip = VideoFileClip(video_path)
            
            if progress_callback:
                progress_callback(30, "Checking duration...")
            
            # Check if video is longer than 60 seconds
            if clip.duration > 60:
                logger.info("Trimming video to 60 seconds")
                clip = clip.subclip(0, 60)
            
            if progress_callback:
                progress_callback(50, "Checking aspect ratio...")
            
            # Get video dimensions
            width, height = clip.size
            
            # Check if aspect ratio needs adjustment (should be 9:16)
            target_aspect_ratio = 9/16
            current_aspect_ratio = width/height
            
            if abs(current_aspect_ratio - target_aspect_ratio) > 0.1:
                logger.info("Adjusting aspect ratio to 9:16")
                # Calculate new dimensions
                if current_aspect_ratio > target_aspect_ratio:
                    # Video is too wide
                    new_width = int(height * target_aspect_ratio)
                    x_center = width / 2
                    crop_x1 = x_center - new_width / 2
                    crop_x2 = x_center + new_width / 2
                    clip = clip.crop(x1=crop_x1, y1=0, x2=crop_x2, y2=height)
                else:
                    # Video is too tall
                    new_height = int(width / target_aspect_ratio)
                    y_center = height / 2
                    crop_y1 = y_center - new_height / 2
                    crop_y2 = y_center + new_height / 2
                    clip = clip.crop(x1=0, y1=crop_y1, x2=width, y2=crop_y2)
            
            # Generate output path
            filename = os.path.basename(video_path)
            output_path = os.path.join(app.config['PROCESSED_FOLDER'], f"shorts_{filename}")
            
            if progress_callback:
                progress_callback(70, "Rendering video...")
            
            # Write the processed video
            clip.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                threads=4,
                preset='fast'
            )
            
            clip.close()
            
            if progress_callback:
                progress_callback(100, "Conversion complete!")
                
            logger.info(f"Video converted and saved to: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Error converting video: {str(e)}")
            if progress_callback:
                progress_callback(0, f"Error: {str(e)}")
            return None

    def authenticate_youtube(self, credentials_json):
        """Authenticate with YouTube API using provided credentials"""
        try:
            # Parse the credentials JSON
            credentials_info = json.loads(credentials_json)
            
            # Save to temporary file
            temp_cred_file = "temp_client_secret.json"
            with open(temp_cred_file, 'w') as f:
                json.dump(credentials_info, f)
            
            # Authenticate
            creds = None
            if os.path.exists('token.pickle'):
                with open('token.pickle', 'rb') as token:
                    creds = pickle.load(token)
            
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        temp_cred_file, 
                        ['https://www.googleapis.com/auth/youtube.upload']
                    )
                    creds = flow.run_local_server(port=0)
                
                with open('token.pickle', 'wb') as token:
                    pickle.dump(creds, token)
            
            # Build the service
            self.youtube_service = build('youtube', 'v3', credentials=creds)
            
            # Clean up
            os.remove(temp_cred_file)
            
            return True, "Authentication successful"
            
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return False, f"Authentication failed: {str(e)}"

    def upload_video(self, file_path, title, description="", category_id="22", 
                    keywords="", privacy_status="private", progress_callback=None):
        """Upload video to YouTube"""
        try:
            if progress_callback:
                progress_callback(10, "Starting upload...")
            
            body = {
                'snippet': {
                    'title': title,
                    'description': description,
                    'tags': keywords.split(',') if keywords else [],
                    'categoryId': category_id
                },
                'status': {
                    'privacyStatus': privacy_status,
                    'selfDeclaredMadeForKids': False
                }
            }
            
            # Create media file upload object
            media = MediaFileUpload(
                file_path,
                chunksize=1024*1024,
                resumable=True
            )
            
            if progress_callback:
                progress_callback(20, "Initiating upload...")
            
            # Create insert request
            request = self.youtube_service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media
            )
            
            response = None
            while response is None:
                if progress_callback:
                    progress_callback(30, "Uploading...")
                
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    if progress_callback:
                        progress_callback(30 + progress * 0.7, f"Uploading... {progress}%")
            
            if progress_callback:
                progress_callback(100, "Upload complete!")
            
            return True, response
            
        except Exception as e:
            if progress_callback:
                progress_callback(0, f"Error: {str(e)}")
            return False, f"Upload failed: {str(e)}"

# Global processor instance
processor = YouTubeShortsProcessor()

# API Routes
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "message": "YouTube Shorts API is running"})

@app.route('/api/download', methods=['POST'])
def download_video():
    try:
        data = request.get_json()
        video_url = data.get('url')
        
        if not video_url:
            return jsonify({"success": False, "error": "No URL provided"}), 400
        
        # Start download in background thread
        def download_task():
            try:
                result = processor.download_video(video_url)
                # You could send a webhook or store the result in a database here
            except Exception as e:
                logger.error(f"Download error: {str(e)}")
        
        thread = threading.Thread(target=download_task)
        thread.daemon = True
        thread.start()
        
        return jsonify({"success": True, "message": "Download started"})
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/process', methods=['POST'])
def process_video():
    try:
        data = request.get_json()
        video_path = data.get('video_path')
        
        if not video_path or not os.path.exists(video_path):
            return jsonify({"success": False, "error": "Invalid video path"}), 400
        
        # Start processing in background thread
        def process_task():
            try:
                result = processor.convert_to_shorts_format(video_path)
                # You could send a webhook or store the result in a database here
            except Exception as e:
                logger.error(f"Processing error: {str(e)}")
        
        thread = threading.Thread(target=process_task)
        thread.daemon = True
        thread.start()
        
        return jsonify({"success": True, "message": "Processing started"})
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/authenticate', methods=['POST'])
def authenticate():
    try:
        data = request.get_json()
        credentials = data.get('credentials')
        
        if not credentials:
            return jsonify({"success": False, "error": "No credentials provided"}), 400
        
        success, message = processor.authenticate_youtube(credentials)
        
        if success:
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "error": message}), 400
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_video():
    try:
        data = request.get_json()
        video_path = data.get('video_path')
        title = data.get('title')
        description = data.get('description', '')
        tags = data.get('tags', '')
        privacy_status = data.get('privacy_status', 'private')
        
        if not video_path or not os.path.exists(video_path) or not title:
            return jsonify({"success": False, "error": "Invalid parameters"}), 400
        
        # Start upload in background thread
        def upload_task():
            try:
                success, result = processor.upload_video(
                    video_path, title, description, 
                    keywords=tags, privacy_status=privacy_status
                )
                # You could send a webhook or store the result in a database here
            except Exception as e:
                logger.error(f"Upload error: {str(e)}")
        
        thread = threading.Thread(target=upload_task)
        thread.daemon = True
        thread.start()
        
        return jsonify({"success": True, "message": "Upload started"})
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/upload_file', methods=['POST'])
def upload_file():
    """Endpoint for direct file upload from iOS app"""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "error": "No file selected"}), 400
        
        # Save the file
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        return jsonify({
            "success": True, 
            "message": "File uploaded successfully",
            "file_path": file_path
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=True)