import re

def clean_parse_script(raw_text: str):
    if not raw_text or not raw_text.strip():
        return []

    structured_indicators = ["vo:", "voiceover:", "visual:", "prompt:"]
    has_structure = any(ind in raw_text.lower() for ind in structured_indicators)
    scenes = []

    if has_structure:
        blocks = re.split(r'(?i)(?=scene\s*\d+|\[scene\s*\d+\]|\bvisual:|\bprompt:|\bvideo:|\bimage:|\bvoiceover:|\bvo:|\baudio:|\btext:)', raw_text)
        current_visual = ""
        current_vo = ""
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            vis_match = re.search(r'(?i)(?:visual|prompt|video|image):\s*(.*)', block, re.DOTALL)
            vo_match = re.search(r'(?i)(?:voiceover|vo|audio|text):\s*(.*)', block, re.DOTALL)
            if vis_match:
                vis_text = vis_match.group(1).strip()
                vis_text = re.split(r'(?i)\b(voiceover|vo|audio|text|visual|prompt|video|image|scene):', vis_text)[0].strip()
                vis_text = vis_text.strip('"\'')
                if vis_text:
                    current_visual = vis_text
            if vo_match:
                vo_text = vo_match.group(1).strip()
                vo_text = re.split(r'(?i)\b(voiceover|vo|audio|text|visual|prompt|video|image|scene):', vo_text)[0].strip()
                vo_text = vo_text.strip('"\'')
                if vo_text:
                    current_vo = vo_text
                    scenes.append({
                        "voiceover": current_vo,
                        "visual_prompt": current_visual if current_visual else None
                    })
                    current_visual = ""
                    current_vo = ""

    if not scenes:
        # Check if line-by-line numbered or separate paragraphs
        lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
        for line in lines:
            # Strip leading numbers like "1.", "1 -", "Scene 1:"
            line_clean = re.sub(r'^(?:scene\s*\d+[:.\-]?|\d+[\.\)\-:]\s*|\[\d+\]\s*)', '', line, flags=re.IGNORECASE).strip()
            # Strip visual notes in brackets like [Visual: mountain sunrise]
            vis_in_brackets = re.search(r'\[(?:visual|prompt|image|video):\s*(.*?)\]', line, re.IGNORECASE)
            vis_prompt = vis_in_brackets.group(1).strip() if vis_in_brackets else None
            vo_clean = re.sub(r'\[.*?\]', '', line_clean).strip()
            if vo_clean and len(vo_clean) > 3:
                scenes.append({
                    "voiceover": vo_clean,
                    "visual_prompt": vis_prompt
                })

    if not scenes:
        # Fallback to standard sentence splitting
        cleaned = re.sub(r'\[.*?\]', '', raw_text)
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', cleaned) if s.strip()]
        for s in sentences:
            if len(s) > 3:
                scenes.append({"voiceover": s, "visual_prompt": None})

    return scenes

# Test with various inputs
t1 = """1. Nature is full of incredible mysteries and breathtaking landscapes.
2. From towering mountains to crystal-clear waters, the earth continues to amaze us."""
print("T1 parsed:", clean_parse_script(t1))

t2 = """Scene 1:
Visual: Majestic mountain peaks bathed in golden sunlight
VO: Nature has always inspired wonder in humankind.

Scene 2:
Visual: Deep blue ocean waves crashing against sea cliffs
VO: The waters cover most of our globe."""
print("\nT2 parsed:", clean_parse_script(t2))
