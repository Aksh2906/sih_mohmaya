from pathlib import Path
import os

os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/ai-leakage-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

out = Path(__file__).resolve().parent
bg, ink, olive, ochre = '#FFFEF6', '#171913', '#606C3B', '#DCA65F'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 15})
fig, ax = plt.subplots(figsize=(14, 8), dpi=200)
fig.patch.set_facecolor(bg)
ax.set_facecolor(bg)
fig.subplots_adjust(left=.10, right=.97, bottom=.25, top=.77)
fig.text(.06, .93, 'COST OF SENSITIVE DATA LEAKED TO AI',
         fontsize=27, fontweight='bold', color=ink)
fig.text(.06, .872, 'Hypothetical loss per incident', fontsize=18, color=olive)

labels = ['Customer PII\nNames, emails & IDs',
          'Financial records\nBank & payment details',
          'Medical records\nPatient health data',
          'Source code & IP\nProprietary business assets']
values = [5, 15, 30, 50]
bars = ax.bar(range(4), values, width=.52,
              color=[ochre, '#D4B078', '#92986B', olive], zorder=3)
for bar, value in zip(bars, values):
    ax.text(bar.get_x() + bar.get_width()/2, value + 1.7,
            f'₹{value} lakh', ha='center', va='bottom', fontsize=22,
            color=ink, fontweight='bold')
ax.set_ylim(0, 60)
ax.set_xlim(-.6, 3.6)
ax.set_ylabel('Loss per incident (₹ lakh)', color=ink, labelpad=15)
ax.set_xticks(range(4), labels)
ax.tick_params(axis='x', length=0, pad=15, labelsize=14, colors=ink)
ax.tick_params(axis='y', length=0, pad=8, colors='#60615B')
ax.yaxis.set_major_locator(MultipleLocator(10))
ax.grid(axis='y', color='#DFDFD4', linewidth=.8, zorder=0)
for side in ['top', 'right', 'left']:
    ax.spines[side].set_visible(False)
ax.spines['bottom'].set_color('#AAA99E')
fig.text(.06, .075, 'ILLUSTRATIVE DATA', color=olive, fontsize=12, fontweight='bold')
fig.text(.06, .038, 'Fabricated scenarios; not measured costs or industry estimates.',
         color='#626359', fontsize=12)
fig.savefig(out / 'ai-data-leakage-cost.png', facecolor=bg)
fig.savefig(out / 'ai-data-leakage-cost.svg', facecolor=bg)
print(out / 'ai-data-leakage-cost.png')
