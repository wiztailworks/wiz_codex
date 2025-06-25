# ──────────────────────────────────────────────
# 📘 Wiz Codex: HP Scanner v0.1β
#
# 本ツールは、Wizardry: The Five Ordeals の実行中プロセスから
# 味方の現在HPに基づいてメモリ内の戦闘用データ領域を特定し、
# 敵グループごとのHPをリアルタイムで可視化します。
#
# ✅ 主な機能:
# - 味方の現在HP（6体分）をGUIで入力し、メモリから構造体を検索
# - 特定したアドレスを保存し、次回以降の再利用が可能
# - 敵HP（最大 6 グループ × 各9体）を 100ms 間隔で更新表示
#
# ⚠️ 注意点:
# - 敵が戦闘から逃走しても、構造体にはHPがキャッシュとして残る場合があります。
# - 敵グループに空きがある場合も、未使用スロットに以前のHP値が残って表示されることがあります。
#   ⇒ 実際の敵数と一致しない可能性があります。
#
# 💾 出力ファイル:
# - locked_hp_struct.csv      … ロックしたデータアドレス情報
# - prev_hp_values.csv        … 入力HPの再利用用キャッシュ
#
# ──────────────────────────────────────────────
# 📗 Wiz Codex: HP Scanner (v0.1β)
#
# This tool scans the memory of Wizardry: The Five Ordeals to locate
# the active in-battle data region by using the party's current HP
# as a signature. Enemy group HPs are then displayed in real time.
#
# ✅ Features:
# - GUI input for 6 party members' current HP, used to scan memory
# - Located memory address is saved and reused on next launch
# - Displays enemy HP (up to 6 groups × 9 members) with 100ms updates
#
# ⚠️ Notes:
# - Even if enemies flee, their HP data may remain cached in memory.
# - Unused enemy slots may display leftover HP values from previous battles.
#
# 💾 Output:
# - locked_hp_struct.csv      … locked address info for the data region
# - prev_hp_values.csv        … cached input HP values for reuse
# ──────────────────────────────────────────────


import tkinter as tk
from tkinter import messagebox
import pymem, ctypes, csv, os

# ──────────────────────────────
VERBOSE = False  # ← 詳細ログ制御用
def vprint(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)

# 基本定数
PROCESS_NAME = "WizardryFoV2.exe"
STRUCT_SIZE  = 0x300          # 今後の拡張用（未使用）
SCAN_STRIDE  = 4              # 今後の拡張用（未使用）
OFFSET_CUR   = 0x000
OFFSET_MAX   = 0x1D20

# ファイル名
CSV_LOCKED_PATH  = "locked_hp_struct.csv"
CSV_PREV_HP_PATH = "prev_hp_values.csv"

# 敵 HP テーブル関連
ENEMY_BASE_OFF   = 0x30
ENEMY_GROUP_STEP = 0x30
ENEMY_SLOT_STEP  = 4
TICK_MS          = 100  # 100 ms ごとに更新

# グループの列ラベルとインデックス
GROUP_ORDER = [
    ("Front", (0, 1)),
    ("Mid",   (2, 3)),
    ("Rear",  (4, 5)),
]

# ──────────────────────────────
# 低レベルヘルパ
def read_u32(b: bytes, offset: int) -> int:
    return int.from_bytes(b[offset:offset+4], "little")

def get_valid_regions(pm):
    """MEM_COMMIT & PAGE_READWRITE 領域を列挙（旧ロジックと同じ == 判定）"""
    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress",        ctypes.c_void_p),
            ("AllocationBase",     ctypes.c_void_p),
            ("AllocationProtect",  ctypes.c_ulong),
            ("RegionSize",         ctypes.c_size_t),
            ("State",              ctypes.c_ulong),
            ("Protect",            ctypes.c_ulong),
            ("Type",               ctypes.c_ulong),
        ]

    MEM_COMMIT     = 0x1000
    PAGE_READWRITE = 0x04
    MEM_ALIGN      = 0x1000

    regions, mbi = [], MEMORY_BASIC_INFORMATION()
    addr = 0
    while addr < 0x7FFFFFFFFFFF:
        res = ctypes.windll.kernel32.VirtualQueryEx(
            pm.process_handle, ctypes.c_void_p(addr),
            ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if not res:
            addr += MEM_ALIGN
            continue

        base = mbi.BaseAddress
        if base:
            addr_val = int(base)
            if mbi.State == MEM_COMMIT and mbi.Protect == PAGE_READWRITE:
                regions.append((addr_val, mbi.RegionSize))
            addr = addr_val + mbi.RegionSize
        else:
            addr += MEM_ALIGN
    return regions

def read_regions_bytes_full(pm, regions, struct_size):
    """リージョンをまとめて読み込む（旧ロジックそのまま）"""
    result = []
    for base, size in regions:
        if size < struct_size:
            continue
        try:
            data = pm.read_bytes(base, size)
            result.append((base, data))
        except Exception as e:
            print(f"⚠️ 読み取り失敗: 0x{base:X}, size={size} → {e}")
    return result

def scan_hp_struct_offsets_signature_partial(region_data, cur_vals, offset_max):
    """HP6体完全一致 → +offset_max 側の値 >= 現在HP ならヒット"""
    matched_addrs = []
    sig_bytes = b''.join(x.to_bytes(4, 'little') for x in cur_vals)

    for idx, (base, data) in enumerate(region_data):
        pos = data.find(sig_bytes)
        while pos != -1:
            if pos + offset_max + 6*4 <= len(data):
                max_vals = [
                    read_u32(data, pos + offset_max + i*4)
                    for i in range(6)
                ]
                if all(m >= c for m, c in zip(max_vals, cur_vals)):
                    addr = base + pos
                    matched_addrs.append(addr)
                    print(f"✅ 候補: 0x{addr:X}")
            pos = data.find(sig_bytes, pos + 1)

        if idx % 10 == 0 or idx == len(region_data)-1:
            print(f"📍 {idx+1}/{len(region_data)} 領域完了（候補数: {len(matched_addrs)}）")
    return matched_addrs

# ──────────────────────────────
# Pymem 1回だけアタッチ → 再利用キャッシュ
pm_cache = {}

def get_pm():
    vprint(f"📦 pm_cache keys = {list(pm_cache.keys())}")
    try:
        pm = pm_cache.get("pm")
        vprint(f"🔍 pm: {pm}, handle valid? {hasattr(pm, 'process_handle') and bool(pm.process_handle)}")
        if pm and pm.process_handle:
            vprint("✅ get_pm: キャッシュ使用中")
            return pm
    except Exception as e:
        print(f"⚠️ get_pm: 例外 → {e}")
        pm_cache.pop("pm", None)

    print("🆕 get_pm: 初回 or 再アタッチ実行")
    pm_cache["pm"] = attach_to_wizardry()
    return pm_cache["pm"]
# ──────────────────────────────

# メインスキャン
def attach_to_wizardry():
    """Wizardry プロセスにアタッチして pymem.Pymem を返す"""
    print("🔄 Wizardryプロセスに接続中...")
    try:
        return pymem.Pymem(PROCESS_NAME)
    except Exception as e:
        raise RuntimeError(f"{PROCESS_NAME} に接続できませんでした: {e}")


def run_hp_scan(cur_vals):
    pm = get_pm()        

    print("📚 有効メモリ領域を列挙中...")
    regions = get_valid_regions(pm)
    print(f"📦 対象領域数: {len(regions)}")

    region_data = read_regions_bytes_full(pm, regions,
                                          struct_size=OFFSET_MAX + 6*4)

    print("🔎 シグネチャスキャン中...")
    matched_addrs = scan_hp_struct_offsets_signature_partial(
        region_data, cur_vals=cur_vals, offset_max=OFFSET_MAX
    )

    if not matched_addrs:
        raise RuntimeError("一致する構造体が見つかりませんでした")

    locked_addr = min(matched_addrs)
    print(f"🔒 本命構造体アドレスをロック: 0x{locked_addr:X}")

    # --- 結果CSV保存 ---
    with open(CSV_LOCKED_PATH, "w", encoding="utf-8", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["cur_hp_addr", "max_hp_addr",
                         "struct_base", "offset_cur", "offset_max"])
        writer.writerow([f"0x{locked_addr+OFFSET_CUR:X}",
                         f"0x{locked_addr+OFFSET_MAX:X}",
                         f"0x{locked_addr:X}",
                         f"0x{OFFSET_CUR:X}",
                         f"0x{OFFSET_MAX:X}"])
    print(f"📝 ロック情報を保存しました → {CSV_LOCKED_PATH}")

    # --- 入力HP保存（再利用用） ---
    try:
        with open(CSV_PREV_HP_PATH, "w", newline='') as f_hp:
            csv.writer(f_hp).writerow(cur_vals)
        print(f"📝 現在HPを保存しました → {CSV_PREV_HP_PATH}")
    except Exception as e:
        print(f"⚠️ 現在HPの保存に失敗しました: {e}")

    return locked_addr  # 後続で即使いたい場合用

# ──────────────────────────────
# 前回HP読み込み
def load_last_hp():
    if not os.path.exists(CSV_PREV_HP_PATH):
        print("⚠️ 前回HPデータが見つかりません")
        return None
    try:
        with open(CSV_PREV_HP_PATH, newline='') as f:
            row = next(csv.reader(f))
            vals = [int(x) for x in row]
            if len(vals) == 6:
                print(f"📥 前回HPを読み込みました: {vals}")
                return vals
            print("⚠️ データ形式不正（6値でない）")
            return None
    except Exception as e:
        print(f"⚠️ HP読み込み失敗: {e}")
        return None

# ──────────────────────────────
# 敵 HP 読み取り
def read_enemy_hp(pm, struct_base):
    groups = []
    for g in range(6):
        base_g = struct_base + ENEMY_BASE_OFF + g * ENEMY_GROUP_STEP
        slots = []
        for s in range(9):
            try:
                val = pm.read_int(base_g + s * ENEMY_SLOT_STEP)
            except Exception as e:
                vprint(f"⚠️ enemy[{g}][{s}] 読み取り失敗 → {e}")
                val = -1  # もしくは None など
            slots.append(val)
        groups.append(slots)
    return groups

def load_struct_base():
    if not os.path.exists(CSV_LOCKED_PATH):
        raise FileNotFoundError("locked_hp_struct.csv がありません")
    with open(CSV_LOCKED_PATH, newline='') as f:
        next(csv.reader(f))          # ヘッダ
        row = next(csv.reader(f))
        addr = int(row[2], 16)       # struct_base 列
        print(f"📡 敵HP構造体アドレス読込成功 → 0x{addr:X}")
        return addr

# ──────────────────────────────
# 味方 HP 読み取り
def read_party_hp(pm, struct_base):
    """
    Return: List[Tuple[cur_hp, max_hp]]  length = 6
    誤認防止のため “fetch_party_hp” に名称変更
    """
    cur = [pm.read_int(struct_base + OFFSET_CUR  + i*4) for i in range(6)]
    max_ = [pm.read_int(struct_base + OFFSET_MAX + i*4) for i in range(6)]
    return list(zip(cur, max_))

def update_party_hp_view(pm, struct_base, widgets):
    """
    widgets: List[Tuple[Canvas, Label]]  ← create_hp_bar_frame() が返すもの
    毎 tick 呼び出してバーを再描画する
    """
    try:
        ally_hp = read_party_hp(pm, struct_base)
    except Exception as e:
        vprint(f"⚠️ read_party_hp 読み取り失敗 → {type(e).__name__}: {e}")
        return  # エラー時は表示を維持して中断

    for (cur, maxhp), (cv, lbl) in zip(ally_hp, widgets):
        try:
            cv.delete("all")

            if maxhp <= 0:  # 空スロ or 読み取り失敗（-1等）
                lbl.config(text="-- / --")
                continue

            percent  = cur / maxhp if maxhp else 0
            bar_len  = int(percent * cv.winfo_width())
            if cur == 0:
                color = 'gray50'
            elif percent > .5:
                color = 'lime'
            elif percent > .25:
                color = 'orange'
            else:
                color = 'red'

            cv.create_rectangle(0, 0, bar_len, 10, fill=color, width=0)
            lbl.config(text=f"{cur} / {maxhp}")

        except Exception as e:
            print(f"⚠️ 味方スロット描画エラー → {type(e).__name__}: {e}")
            lbl.config(text="ERR / ERR")


# ──────────────────────────────
# GUI
def create_hp_bar_frame(root):
    """
    味方6人分のHPバーとラベルを生成して返す
    Return: Frame, List[Tuple[Canvas, Label]]
    """
    frame = tk.Frame(root, relief="groove", bd=2)
    tk.Label(frame, text="Party HP (auto-refresh)").pack(anchor="w")

    widgets = []
    for i in range(6):
        row = tk.Frame(frame)
        row.pack(anchor="w", padx=5, pady=1)

        label = tk.Label(row, text="-- / --", width=10)
        label.pack(side="left")

        canvas = tk.Canvas(row, width=120, height=10)
        canvas.pack(side="left", padx=5)

        widgets.append((canvas, label))

    return frame, widgets



def launch_hp_scan_gui():
    # --- コールバック ---
    def on_lock():
        try:
            cur_vals = [int(e.get()) for e in entries]
            if len(cur_vals) != 6:
                raise ValueError

            # ここでキャッシュを明示的に破棄 → get_pm() が強制再アタッチ
            pm_cache.pop("pm", None)
            struct_base_holder.pop("base", None)

            run_hp_scan(cur_vals)
            messagebox.showinfo("完了", f"ロック成功！\n{CSV_LOCKED_PATH}")
        except ValueError:
            messagebox.showerror("入力エラー", "6体分の整数を入力してください")
        except Exception as e:
            messagebox.showerror("失敗", str(e))
    def on_load_prev():
        vals = load_last_hp()
        if not vals:
            messagebox.showwarning("読み込み失敗", "前回のHP値が見つかりません")
            return
        for ent, v in zip(entries, vals):
            ent.delete(0, tk.END)
            ent.insert(0, str(v))



    # --- ウィンドウ ---
    root = tk.Tk()
    root.title("Wiz Codex: Lifebook")

    # 共通コンテナ（縦に並べるだけ）
    body = tk.Frame(root)
    body.pack()

    # HPバー生成
    party_frame, party_widgets = create_hp_bar_frame(body)
    party_frame.grid(row=0, column=0, pady=4, sticky="w")

    # --- 敵 HP 表示（body 配下に置く） ---
    enemy_frame = tk.Frame(body, relief="groove", bd=2)
    enemy_frame.grid(row=1, column=0, padx=5, pady=5, sticky="w")

    tk.Label(enemy_frame, text="Enemy HP (auto-refresh)").pack(anchor="w")

    enemy_labels = []
    for section, groups in GROUP_ORDER:
        tk.Label(enemy_frame, text=section).pack(anchor="w")
        for g in groups:
            var = tk.StringVar(value=f"G{g}: " + " ".join(["----"]*9))
            lbl = tk.Label(enemy_frame, textvariable=var, font=("Consolas", 9))
            lbl.pack(anchor="w", padx=10)
            enemy_labels.append((g, var))

    
    # --- 表示切り替えフラグ ---
    party_hp_visible = tk.IntVar(value=1)

    def toggle_party_hp_view():
        if party_hp_visible.get():
            party_frame.grid()          # 以前の row/col でそのまま復活
        else:
            party_frame.grid_remove()   # 配置情報を保持したまま非表示



    # --- 「常に最前面」チェックの状態を保持 ---
    topmost_var = tk.IntVar(value=0)
    def toggle_topmost():
        # 1 なら最前面、0 なら通常
        root.attributes('-topmost', bool(topmost_var.get()))

    tk.Label(root, text="戦闘中に現在HPを入力（空きは 0）").pack(pady=6)

    frame, entries = tk.Frame(root), []
    frame.pack()
    for i in range(6):
        tk.Label(frame, text=f"{i+1}人目:").grid(row=0, column=2*i)
        ent = tk.Entry(frame, width=6, justify="center")
        ent.insert(0, "0")
        ent.grid(row=0, column=2*i+1)
        entries.append(ent)

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=10)

    tk.Button(btn_frame, text="🗘 前回値を読み込み", command=on_load_prev).grid(row=0, column=0, padx=5)
    tk.Button(btn_frame, text="🔒 スキャン開始（戦闘中）", command=on_lock).grid(row=0, column=1, padx=5)
    tk.Checkbutton(btn_frame, text="常に最前面", variable=topmost_var, command=toggle_topmost).grid(row=0, column=2, padx=5)
    tk.Checkbutton(btn_frame, text="味方HPバー表示", variable=party_hp_visible, command=toggle_party_hp_view).grid(row=1, column=0, columnspan=2, pady=4)




    # --- HP モニタリング ---
    struct_base_holder = {}   # 一度だけ読み込み、ここに保持

    def update_hp_ui():
        global pm_cache
        try:
            pm = get_pm()                         # ← 直接取得
            if "base" not in struct_base_holder:
                struct_base_holder["base"] = load_struct_base()

            base = struct_base_holder["base"]

            # 敵 HP 更新（-1 は ---- と表示）
            enemy_hp = read_enemy_hp(pm, base)
            for g, var in enemy_labels:
                text = "G{}: ".format(g) + " ".join(
                    f"{v:4}" if v >= 0 else "----" for v in enemy_hp[g]
                )
                var.set(text)

            # 味方 HP 更新
            update_party_hp_view(pm, base, party_widgets)

        except Exception as e:
            print(f"⚠️ update_hp_ui: エラー種別 → {type(e).__name__}, 内容 → {e!r}")
            pm_cache.pop("pm", None)
            struct_base_holder.pop("base", None)

        root.after(TICK_MS, update_hp_ui)



    update_hp_ui()
    root.mainloop()

# ──────────────────────────────

if __name__ == "__main__":
    launch_hp_scan_gui()
