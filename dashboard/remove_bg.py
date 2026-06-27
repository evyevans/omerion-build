from PIL import Image

def remove_white_bg(input_path, output_path):
    img = Image.open(input_path).convert("RGBA")
    datas = img.getdata()
    
    newData = []
    # Tolerance for off-white
    threshold = 240
    for item in datas:
        # If the pixel is very bright (almost white), make it fully transparent
        if item[0] >= threshold and item[1] >= threshold and item[2] >= threshold:
            newData.append((255, 255, 255, 0))
        else:
            newData.append(item)
            
    img.putdata(newData)
    img.save(output_path, "PNG")

remove_white_bg('/Users/evy/.gemini/antigravity/brain/f4ec9bee-5761-4859-8633-6e0e64a6a59d/agent_front_1779102488482.png', 'public/agent-front.png')
remove_white_bg('/Users/evy/.gemini/antigravity/brain/f4ec9bee-5761-4859-8633-6e0e64a6a59d/agent_tilt_clean_1779102546323.png', 'public/agent-tilt.png')
remove_white_bg('/Users/evy/.gemini/antigravity/brain/f4ec9bee-5761-4859-8633-6e0e64a6a59d/agent_back_1779102525696.png', 'public/agent-back.png')
print("Backgrounds successfully removed!")
