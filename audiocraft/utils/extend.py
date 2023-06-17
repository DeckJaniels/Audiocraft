import torch
import math
from audiocraft.models import MusicGen
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageColor
import string
import tempfile
import os
import textwrap
import requests
from io import BytesIO
from huggingface_hub import hf_hub_download


INTERRUPTING = False

def separate_audio_segments(audio, segment_duration=30, overlap=1):
    sr, audio_data = audio[0], audio[1]

    total_samples = len(audio_data)
    segment_samples = sr * segment_duration
    overlap_samples = sr * overlap

    segments = []
    start_sample = 0

    while total_samples >= segment_samples:
        # Collect the segment
        # the end sample is the start sample plus the segment samples, 
        # the start sample, after 0, is minus the overlap samples to account for the overlap        
        end_sample = start_sample + segment_samples
        segment = audio_data[start_sample:end_sample]
        segments.append((sr, segment))

        start_sample += segment_samples - overlap_samples
        total_samples -= segment_samples

    # Collect the final segment
    if total_samples > 0:
        segment = audio_data[-segment_samples:]
        segments.append((sr, segment))
    print(f"separate_audio_segments: {len(segments)} segments")
    return segments

def generate_music_segments(text, melody, MODEL, seed, duration:int=10, overlap:int=1, segment_duration:int=30):
    # generate audio segments
    melody_segments = separate_audio_segments(melody, segment_duration, 0) 
    
    # Create a list to store the melody tensors for each segment
    melodys = []
    output_segments = []
    last_chunk = []
    text += ", seed=" + str(seed)
    
    # Calculate the total number of segments
    total_segments = max(math.ceil(duration / segment_duration),1)
    #calc excess duration
    excess_duration = segment_duration - (total_segments * segment_duration - duration)
    print(f"total Segments to Generate: {total_segments} for {duration} seconds. Each segment is {segment_duration} seconds. Excess {excess_duration}")

    # If melody_segments is shorter than total_segments, repeat the segments until the total_segments is reached
    if len(melody_segments) < total_segments:
        #fix melody_segments
        for i in range(total_segments - len(melody_segments)):
            segment = melody_segments[i]
            melody_segments.append(segment)
        print(f"melody_segments: {len(melody_segments)} fixed")

    # Iterate over the segments to create list of Meldoy tensors
    for segment_idx in range(total_segments):
        if INTERRUPTING:
            return [], duration
        print(f"segment {segment_idx + 1} of {total_segments} \r")
        sr, verse = melody_segments[segment_idx][0], torch.from_numpy(melody_segments[segment_idx][1]).to(MODEL.device).float().t().unsqueeze(0)

        print(f"shape:{verse.shape} dim:{verse.dim()}")
        if verse.dim() == 2:
            verse = verse[None]
        verse = verse[..., :int(sr * MODEL.lm.cfg.dataset.segment_duration)]
        # Append the segment to the melodys list
        melodys.append(verse)

    torch.manual_seed(seed)
    for idx, verse in enumerate(melodys):
        if INTERRUPTING:
            return output_segments, duration - (segment_duration * len(output_segments))
            
        print(f"Generating New Melody Segment {idx + 1}: {text}\r")
        if output_segments:
            # If this isn't the first segment, use the last chunk of the previous segment as the input
            last_chunk = output_segments[-1][:, :, -overlap*MODEL.sample_rate:]
        output = MODEL.generate_with_all(
            descriptions=[text],
            melody_wavs=verse,
            sample_rate=sr,
            progress=False,
            prompt=last_chunk if len(last_chunk) > 0 else None,
        )

        # Append the generated output to the list of segments
        #output_segments.append(output[:, :segment_duration])
        output_segments.append(output)
        print(f"output_segments: {len(output_segments)}: shape: {output.shape} dim {output.dim()}")
    return output_segments, excess_duration

def save_image(image):
    """
    Saves a PIL image to a temporary file and returns the file path.

    Parameters:
    - image: PIL.Image
        The PIL image object to be saved.

    Returns:
    - str or None: The file path where the image was saved,
        or None if there was an error saving the image.

    """
    temp_dir = tempfile.gettempdir()
    temp_file = tempfile.NamedTemporaryFile(suffix=".png", dir=temp_dir, delete=False)
    temp_file.close()
    file_path = temp_file.name

    try:
        image.save(file_path)
        
    except Exception as e:
        print("Unable to save image:", str(e))
        return None
    finally:
        return file_path

def hex_to_rgba(hex_color):
    try:
        # Convert hex color to RGBA tuple
        rgba = ImageColor.getcolor(hex_color, "RGBA")
    except ValueError:
        # If the hex color is invalid, default to yellow
        rgba = (255,255,0,255)
    return rgba

def load_font(font_name, font_size=16):
    """
    Load a font using the provided font name and font size.

    Parameters:
        font_name (str): The name of the font to load. Can be a font name recognized by the system, a URL to download the font file,
            a local file path, or a Hugging Face model hub identifier.
        font_size (int, optional): The size of the font. Default is 16.

    Returns:
        ImageFont.FreeTypeFont: The loaded font object.

    Notes:
        This function attempts to load the font using various methods until a suitable font is found. If the provided font_name
        cannot be loaded, it falls back to a default font.

        The font_name can be one of the following:
        - A font name recognized by the system, which can be loaded using ImageFont.truetype.
        - A URL pointing to the font file, which is downloaded using requests and then loaded using ImageFont.truetype.
        - A local file path to the font file, which is loaded using ImageFont.truetype.
        - A Hugging Face model hub identifier, which downloads the font file from the Hugging Face model hub using hf_hub_download
          and then loads it using ImageFont.truetype.

    Example:
        font = load_font("Arial.ttf", font_size=20)
    """
    font = None
    if not "http" in font_name:
        try:
            font = ImageFont.truetype(font_name, font_size)
        except (FileNotFoundError, OSError):
            print("Font not found. Using Hugging Face download..\n")

        if font is None:
            try:
                font_path = ImageFont.truetype(hf_hub_download(repo_id=os.environ.get('SPACE_ID', ''), filename="assets/" + font_name, repo_type="space"), encoding="UTF-8")        
                font = ImageFont.truetype(font_path, font_size)
            except (FileNotFoundError, OSError):
                print("Font not found. Trying to download from local assets folder...\n")
        if font is None:
            try:
                font = ImageFont.truetype("assets/" + font_name, font_size)
            except (FileNotFoundError, OSError):
                print("Font not found. Trying to download from URL...\n")

    if font is None:
        try:
            req = requests.get(font_name)
            font = ImageFont.truetype(BytesIO(req.content), font_size)  
        except (FileNotFoundError, OSError):            
            print(f"Font not found: {font_name} Using default font\n")
    if font:
        print(f"Font loaded {font.getname()}")
    else:
        font = ImageFont.load_default()
    return font


def add_settings_to_image(title: str = "title", description: str = "", width: int = 768, height: int = 512, background_path: str = "", font: str = "arial.ttf", font_color: str = "#ffffff"):
    # Create a new RGBA image with the specified dimensions
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    # If a background image is specified, open it and paste it onto the image
    if background_path == "":
        background = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    else:
        background = Image.open(background_path).convert("RGBA")

    #Convert font color to RGBA tuple
    font_color = hex_to_rgba(font_color)

    # Calculate the center coordinates for placing the text
    text_x = width // 2
    text_y = height // 2
    # Draw the title text at the center top
    title_font = load_font(font, 26)  # Replace with your desired font and size

    title_text = '\n'.join(textwrap.wrap(title, width // 12))
    title_x, title_y, title_text_width, title_text_height = title_font.getbbox(title_text)
    title_x = max(text_x - (title_text_width // 2), title_x, 0)
    title_y = text_y - (height // 2) + 10  # 10 pixels padding from the top
    title_draw = ImageDraw.Draw(image)
    title_draw.multiline_text((title_x, title_y), title, fill=font_color, font=title_font, align="center")
    # Draw the description text two lines below the title
    description_font = load_font(font, 16)  # Replace with your desired font and size
    description_text = '\n'.join(textwrap.wrap(description, width // 12))
    description_x, description_y, description_text_width, description_text_height = description_font.getbbox(description_text)
    description_x = max(text_x - (description_text_width // 2), description_x, 0)
    description_y = title_y + title_text_height + 20  # 20 pixels spacing between title and description
    description_draw = ImageDraw.Draw(image)
    description_draw.multiline_text((description_x, description_y), description_text, fill=font_color, font=description_font, align="center")
    # Calculate the offset to center the image on the background
    bg_w, bg_h = background.size
    offset = ((bg_w - width) // 2, (bg_h - height) // 2)
    # Paste the image onto the background
    background.paste(image, offset, mask=image)

    # Save the image and return the file path
    return save_image(background)