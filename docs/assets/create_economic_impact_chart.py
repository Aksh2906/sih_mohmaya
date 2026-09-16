from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import csv

OUT = Path(__file__).parent
im = Image.new('RGB', (2400, 1350), 'white')
d = ImageDraw.Draw(im)
def txt(x, y, s, size=32, color='#243449', bold=False):
    name = 'Arial Bold.ttf' if bold else 'Arial.ttf'
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/' + name, size)
    d.text((x, y), s, fill=color, font=font)

txt(125, 65, 'What reducing breach risk could be worth', 64)
txt(128, 153, 'Dev Privacy Guard  |  Economic sensitivity analysis — not measured product results', 33, '#59687A')
d.rounded_rectangle((125, 235, 2275, 410), radius=22, fill='#EDF5F2')
txt(165, 261, 'US$46,300', 63, '#347B67', True)
txt(655, 268, 'Annual expected loss avoided per organization', 37)
txt(655, 328, 'IF relevant annual breach probability falls by 1 percentage point', 30, '#496456')

left, right, top, bottom = 285, 2190, 535, 955
max_y = 150000
def y(v):
    return bottom-round((bottom-top)*v/max_y)
txt(155, 465, 'Gross annual expected loss avoided (US$)', 29, '#59687A')
for v in (0, 50000, 100000, 150000):
    yy = y(v)
    d.line((left, yy, right, yy), fill='#E3E9ED', width=2)
    txt(135, yy-16, f'${v//1000}k', 28, '#59687A')

rows=[]
for i,(pp,col) in enumerate(zip((0,1,2,3),('#B9C7D3','#BBD8CD','#8DBFAE','#639F8D'))):
    center=490+i*480
    value=round(4_630_000*pp/100)
    if value:
        d.rectangle((center-120,y(value),center+120,bottom),fill=col)
    else:
        d.line((center-120,bottom,center+120,bottom), fill=col,width=5)
    s=f'${value:,}'
    txt(center-90,y(value)-53,s,37)
    txt(center-68,bottom+28,f'{pp} pp',35)
    rows.append((pp, value))
txt(670, 1040, 'Assumed absolute reduction in annual breach probability',32,'#59687A')
txt(570, 1090, 'Example: 10% to 9% is 1 percentage point (pp), not a measured baseline.',27,'#59687A')
d.line((125,1160,2275,1160),fill='#DDE4E9',width=2)
txt(125,1180,'Benchmark: IBM Cost of a Data Breach Report 2025, Fig. 31 — $4.63M average cost with shadow AI involved.',25,'#59687A')
txt(125,1220,'Model: probability reduction × $4.63M. Illustrative scenarios; applicability to your users must be validated.',25,'#59687A')
txt(125,1260,'Gross risk benefit, not guaranteed cash savings. Subtract deployment, hosting, review and maintenance costs for net benefit.',25,'#59687A')
im.save(OUT/'dev-privacy-guard-economic-scenarios.png')
with (OUT/'dev-privacy-guard-economic-scenarios.csv').open('w',newline='') as f:
    w=csv.writer(f)
    w.writerow(['assumed_absolute_annual_probability_reduction_percentage_points','gross_annual_expected_loss_avoided_usd'])
    w.writerows(rows)
assert rows == [(0,0),(1,46300),(2,92600),(3,138900)]
print(OUT/'dev-privacy-guard-economic-scenarios.png')
