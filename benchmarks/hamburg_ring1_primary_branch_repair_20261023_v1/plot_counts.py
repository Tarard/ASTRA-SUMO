"""Plot the frozen paired native counts without smoothing or missing-value fill."""
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    out = Path(__file__).resolve().parent
    data = json.loads((out / 'plot-data.json').read_bytes())
    fields = sorted({r['field'] for r in data['rows']})
    assert len(fields) == 13 and len(data['rows']) == 130
    with plt.rc_context({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False}):
        fig, axes = plt.subplots(5, 3, figsize=(14, 16), layout='constrained')
        for ax, field in zip(axes.flat, fields):
            rows = sorted((r for r in data['rows'] if r['field'] == field), key=lambda r: r['begin'])
            assert len(rows) == 10 and len({r['begin'] for r in rows}) == 10
            x = list(range(10))
            ax.plot(x, [r['observed'] for r in rows], 'o-', color='black', label='Observed')
            ax.plot(x, [r['before'] for r in rows], 's--', color='#0072B2', label='Before: 236 roads')
            ax.plot(x, [r['after'] for r in rows], '^:', color='#D55E00', label='After: 238 roads')
            ax.set(title=field.replace('hh_', ''), ylabel='Vehicles / 15 min', ylim=(0, None),
                   xticks=[0, 3, 6, 9], xticklabels=['07:30', '08:15', '09:00', '09:45'])
            ax.set_xlabel('Bin start, CEST, 19 Oct 2026')
            ax.grid(axis='y', alpha=.2)
        for ax in list(axes.flat)[13:]:
            ax.set_visible(False)
        fig.legend(*axes.flat[0].get_legend_handles_labels(), loc='outside lower center', ncol=3)
        fig.suptitle('Hamburg Ring1: all 130 native E1 nVehContrib values\n'
                     'Paired 8640 requests; MAE 14.0308 to 9.8385; calibration incomplete', fontsize=15)
        fig.savefig(out / 'counts130.png', dpi=160)
        plt.close(fig)
    rows = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(r[k]))}</td>' for k in
        ('field', 'local_start', 'observed', 'before', 'after', 'residual')) + '</tr>' for r in data['rows'])
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>汉堡主路修复与真实计数</title>
<style>body{font:16px/1.6 "Segoe UI",sans-serif;max-width:1180px;margin:40px auto;padding:0 20px;color:#202833}
img{width:100%;height:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px;border-bottom:1px solid #ddd;text-align:right}th:first-child,td:first-child{text-align:left}
.state{padding:16px;background:#fff4d6;border-left:5px solid #a35d00}a{color:#075ca5}</style>
<h1>汉堡主路修复：已改善，尚未完成标定</h1>
<p>相同8640辆请求、出发时间及真实观测。只恢复源中两条Alsterglacis主路、必要连接和源控制，2589辆在检测点后的尾段改走主路。</p>
<table><tr><th>指标</th><th>修前</th><th>修后</th></tr>
<tr><td>130项原生E1计数MAE</td><td>14.030769</td><td>9.838462</td></tr>
<tr><td>最大绝对误差</td><td>66</td><td>47</td></tr>
<tr><td>40项派生站点MAE</td><td>43.700</td><td>27.575</td></tr>
<tr><td>14400秒等待进入</td><td>744</td><td>178</td></tr>
<tr><td>14400秒在途</td><td>310</td><td>227</td></tr>
<tr><td>碰撞 / 传送 / 丢弃</td><td>0 / 0 / 0</td><td>0 / 0 / 0</td></tr></table>
<p class="state">阶段目标已达到；MAE仍不为零，运行仍有积压，不能称为有效标定。主路分流比例属于先验，现场SPaT未确认。保留LSA535缺绿灯及紧急制动警告。</p>
<p>旧README的40项进入记录去重计数已经归零，属于不同日期和计数定义，不能替代本页130项原生计数。</p>
<h2>全部真实与仿真计数</h2><p>每点是一项15分钟计数，不作平滑、不填零。各字段纵轴独立，均从零起。站点值是字段求和，不是独立证据。</p>
<img src="counts130.png" alt="13字段各10个时段的真实计数、修前及修后原生完整通过计数。全部残差见下表。">
<h2>固定航片上的道路对照</h2><p>青色为车道，粉色为内部连接。左为236道路，右为恢复两条主路后的238道路。图像审查状态为review_required。</p>
<img src="figures/alsterglacis-comparison.png" alt="相同官方2026航片范围内，修前缺少主路连接，修后恢复源主路连接。">
<p>航片：Freie und Hansestadt Hamburg, LGV；Datenlizenz Deutschland Namensnennung 2.0。图像不证明现场几何或控制对应。</p>
<h2>全部130项残差</h2><table><tr><th>字段</th><th>CEST起点</th><th>真实</th><th>修前</th><th>修后</th><th>修后−真实</th></tr>'''
    (out / 'report.html').write_text(page + rows + '</table></html>\n', encoding='utf8')


if __name__ == '__main__':
    main()
