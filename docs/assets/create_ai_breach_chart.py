from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import csv

OUT = Path(__file__).parent
W, H = 2400, 1350
im = Image.new('RGB', (W, H), '#FFFFFF')
d = ImageDraw.Draw(im)
font_path = '/System/Library/Fonts/Supplemental/Arial.ttf'
bold_path = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
def text(x, y, value, size=32, fill='#203149', bold=False):
    d.text((x, y), value, font=ImageFont.truetype(bold_path if bold else font_path, size), fill=fill)

text(125, 75, 'The financial cost of AI-related data breaches', 64)
text(128, 165, 'Average cost per breach  |  US$ millions  |  IBM / Ponemon Institute, 2025', 33, '#586779')
d.rounded_rectangle((125, 250, 2275, 440), radius=20, fill='#F0EBFA')
text(170, 282, 'US$4.63 million', 64, '#65508B', True)
text(830, 285, 'Average breach cost when shadow AI was involved', 37)
text(830, 347, 'Shadow AI = AI used without employer approval or oversight', 30, '#586779')

rows = [
    ('Incidents targeting an', 'AI model', 4.46, '#A9C9E6'),
    ('AI used to execute', 'the security incident', 4.49, '#A6D1C6'),
    ('Unsanctioned / shadow AI', 'involved in the breach', 4.63, '#A994CB'),
]
x0, x1, top, bottom = 650, 2080, 545, 1010
scale = (x1-x0)/5
for tick in range(6):
    x = round(x0 + tick*scale)
    d.line((x, top, x, bottom), fill='#E2E7ED', width=2)
    text(x-10, bottom+23, str(tick), 29, '#586779')
for idx,(a,b,val,color) in enumerate(rows):
    y = 565 + idx*150
    text(130, y+13, a, 32)
    text(130, y+54, b, 32)
    end = round(x0+val*scale)
    d.rectangle((x0, y+7, end, y+103), fill=color)
    text(end+25, y+29, f'${val:.2f}M', 39)
text(1030, 1070, 'Average breach cost (US$ millions)', 30, '#586779')
d.line((125, 1150, 2275, 1150), fill='#DCE2EA', width=2)
text(125, 1180, 'Source: IBM Cost of a Data Breach Report 2025, Figure 31, printed p. 37.', 27, '#586779')
text(125, 1223, 'Study: 600 breached organizations; March 2024–February 2025. Category sample sizes not shown in Figure 31.', 25, '#586779')
text(125, 1264, 'Scope: AI-related breach costs; not a causal estimate of losses from browser automation. Categories may overlap.', 25, '#586779')
im.save(OUT / 'ai-data-breach-costs-2025.png')
with (OUT / 'ai-data-breach-costs-2025.csv').open('w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['category', 'average_cost_usd_millions', 'source_figure'])
    for a,b,val,_ in rows:
        writer.writerow([a+' '+b, val, 'IBM Cost of a Data Breach Report 2025, Figure 31'])
print(OUT / 'ai-data-breach-costs-2025.png')
