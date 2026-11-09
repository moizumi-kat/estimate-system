# -*- coding: utf-8 -*-
"""端子番号入りシーケンス図のPDF出力(印刷用)。

確定した端子番号を元のシーケンス図に青字で重ね、A3横のPDF(シート毎に1ページ)にする。
描画は元図を黒で描き(確認しやすさ)、その上に端子番号を青で重ねる(色方針の2パス問題を回避)。
密集箇所(動力ラグ 1/3/5・2/4/6 や OL 95-98)は縦に段積みして潰れを防ぐ(DXF記入と同じ考え方)。
"""
import os
import ezdxf


def _setup_mpl():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.font_manager as fm
    try:
        fm.fontManager.addfont('/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf')
        matplotlib.rcParams['font.family'] = 'IPAGothic'
    except Exception:
        pass


def render_pdf(seq_paths, skel_paths, seiban, out_pdf, numbers=None):
    """各シート(制御シート)を描画し、端子番号を青字で重ねてPDF化する。
    numbers: {(シート名, round(x), round(y)): 端子番号}。保存済みの確定値があれば渡す。
             無ければ自動値(_all_terminals_by_sheet の結果)を使う。戻り: ページ数。"""
    from . import stage_interactive as SI
    _setup_mpl()
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.backends.backend_pdf import PdfPages
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing import config as C
    halo = [pe.withStroke(linewidth=1.2, foreground='white')]
    numbers = numbers or {}
    sheets = SI._all_terminals_by_sheet(seq_paths, skel_paths)
    pages = 0
    with PdfPages(out_pdf) as pdf:
        for path, items in sheets:
            name = os.path.basename(path)
            # 端子番号(確定値があれば差し替え)。空/「?」は印字しない。
            labeled = []
            for it in items:
                num = numbers.get((name, round(it['x']), round(it['y'])), it.get('num', ''))
                num = '' if str(num) in ('?', '', 'None') else str(num)
                if num:
                    labeled.append({'x': it['x'], 'y': it['y'], 'num': num})
            pts = [(it['x'], it['y']) for it in items] or [(0, 0), (1000, 1000)]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            mx = (max(xs) - min(xs)) * 0.05 + 150
            my = (max(ys) - min(ys)) * 0.05 + 150
            x0, x1 = min(xs) - mx, max(xs) + mx
            y0, y1 = min(ys) - my, max(ys) + my
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            fig = plt.figure()
            ax = fig.add_axes([0.01, 0.03, 0.98, 0.94])
            ax.set_axis_off()
            Frontend(RenderContext(doc), MatplotlibBackend(ax),
                     config=C.Configuration(color_policy=C.ColorPolicy.BLACK)).draw_layout(msp, finalize=True)
            # 端子番号を青で重ねる。密集(同一アンカー)は縦に段積み。
            span = max(x1 - x0, y1 - y0)
            step = span * 0.010           # 段積み間隔(図面スケール比)
            groups = {}
            for it in labeled:
                groups.setdefault((round(it['x'] / 35.0), round(it['y'] / 35.0)), []).append(it)
            for grp in groups.values():
                seen, uniq = set(), []
                for it in grp:
                    if it['num'] in seen:
                        continue
                    seen.add(it['num'])
                    uniq.append(it)
                uniq.sort(key=lambda it: it['num'])
                top = (len(uniq) - 1) / 2.0
                for j, it in enumerate(uniq):
                    oy = it['y'] + span * 0.006 + (top - j) * step
                    ax.text(it['x'] + span * 0.004, oy, it['num'], color='#1565c0',
                            fontsize=4.2, fontweight='bold', ha='left', va='center', zorder=100,
                            clip_on=True, path_effects=halo)
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_aspect('equal')
            ax.set_axis_off()
            ax.set_title(f'{seiban}　{name}　端子番号記入図', fontsize=9, loc='left')
            fig.set_size_inches(16.54, 11.69)        # A3 横
            pdf.savefig(fig, facecolor='white')
            plt.close(fig)
            pages += 1
    return pages


def render_dxf_pdf(dxf_paths, out_pdf, title_prefix=''):
    """任意のDXF一式をそのままA3横のPDFに描画する(ロケーター付図面など)。
    元図は黒で描く(確認しやすさ)。各DXF=1ページ。戻り: ページ数。"""
    _setup_mpl()
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing import config as C
    pages = 0
    with PdfPages(out_pdf) as pdf:
        for path in dxf_paths:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            fig = plt.figure()
            ax = fig.add_axes([0.01, 0.03, 0.98, 0.94])
            ax.set_axis_off()
            Frontend(RenderContext(doc), MatplotlibBackend(ax),
                     config=C.Configuration(color_policy=C.ColorPolicy.BLACK)).draw_layout(msp, finalize=True)
            ax.set_aspect('equal')
            ax.autoscale()
            ax.set_axis_off()
            ax.set_title(f'{title_prefix}{os.path.basename(path)}', fontsize=9, loc='left')
            fig.set_size_inches(16.54, 11.69)        # A3 横
            pdf.savefig(fig, facecolor='white')
            plt.close(fig)
            pages += 1
    return pages


def render_locator_pdf(layout_paths, out_pdf, rows, title_prefix=''):
    """配置図にロケーター名を赤字で重ねてA3横PDFにする。
    元図は黒で描き、ロケーター名(例 1-B)を小さな赤字で機器位置に重ねる(ポイント指定なので
    機器が密集していても潰れない)。rows: locator_apply.locator_rows() の戻り
    [(loc, sym, x, y, ...), ...]。戻り: ページ数。"""
    _setup_mpl()
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.backends.backend_pdf import PdfPages
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing import config as C
    halo = [pe.withStroke(linewidth=1.3, foreground='white')]
    pages = 0
    with PdfPages(out_pdf) as pdf:
        for path in layout_paths:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            fig = plt.figure()
            ax = fig.add_axes([0.01, 0.03, 0.98, 0.94])
            ax.set_axis_off()
            Frontend(RenderContext(doc), MatplotlibBackend(ax),
                     config=C.Configuration(color_policy=C.ColorPolicy.BLACK)).draw_layout(msp, finalize=True)
            for r in rows:
                loc, x, y = r[0], r[2], r[3]
                letter = str(loc).rsplit('-', 1)[-1]        # 手本に合わせ機器のロケーター字(例 1-B→B)
                ax.text(x, y, letter, color='#d00000', fontsize=9, fontweight='bold',
                        ha='center', va='center', zorder=100, clip_on=True, path_effects=halo)
            ax.autoscale()
            ax.set_aspect('equal')
            ax.set_axis_off()
            ax.set_title(f'{title_prefix}{os.path.basename(path)}', fontsize=9, loc='left')
            fig.set_size_inches(16.54, 11.69)        # A3 横
            pdf.savefig(fig, facecolor='white')
            plt.close(fig)
            pages += 1
    return pages


def numbers_from_final(final_rows):
    """保存した最終端子番号リスト(先頭ヘッダの行列)から {(シート,round(x),round(y)): 端子番号}。"""
    if not final_rows:
        return {}
    head = final_rows[0]
    try:
        si, sx, sy, sn = head.index('シート'), head.index('X'), head.index('Y'), head.index('端子番号')
    except ValueError:
        return {}

    def _f(v):
        try:
            return round(float(v))
        except Exception:
            return 0
    out = {}
    for r in final_rows[1:]:
        if len(r) > max(si, sx, sy, sn):
            out[(r[si], _f(r[sx]), _f(r[sy]))] = r[sn]
    return out
