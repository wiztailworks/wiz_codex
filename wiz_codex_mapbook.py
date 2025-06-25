# === 🧰 標準ライブラリ ===
import csv
import os
import struct
import threading
import time
import sys
import ctypes
import re

# === 🧠 外部ライブラリ（要インストール）===
import pymem
import pyautogui
from PIL import Image, ImageTk

# === 🪟 Win32API 系（pywin32）===
import win32gui
import win32process
import win32con

# === 🖼️ GUI（Tkinter）===
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List

# ================================
# 📦 DIRスキャナ機能：内包版
# ================================
def run_dir_scan():
    """
    dir_scanner.py の機能を内包関数として再構築。
    外部実行せずに直接呼び出す方式。
    """

    # =============================
    # 🔧 構造体スキャン用 グローバル定数
    # =============================

    # ゲームウィンドウタイトル定義
    WINDOW_TITLE = "WizardryFoV2"

    # menu_struct 期待値（初期スキャン用）
    MENU_STRUCT_STATE = 0xD2  # menu_state フィールド値（ESC押下で遷移する中断メニュー）
    MENU_STRUCT_CURSOR = 0x0B  # menu_cursor フィールド値（カーソル位置　最下部選択中）

    # menu_struct フィルタ値（C8でアイドル中の状態）
    MENU_IDLE_STATE = 0xC8  # menu_state フィールド値（ダンジョン内アイドル中）
    MENU_IDLE_CURSOR = 0x00  # menu_cursor フィールド値（非選択 or 最上部選択中）

    # --- 構造体オフセット定義（DIR構造体中の相対位置） ---
    OFFSET_STATE = 0x00     # menu_state の位置
    OFFSET_CURSOR = 0x04    # menu_cursor の位置
    OFFSET_DIR = 0x4C       # dir_val の位置（方向値: 0〜3）
    OFFSET_X = 0x50         # X座標
    OFFSET_Y = 0x54         # Y座標
    OFFSET_FLOOR = 0x58     # 階層（floor）


    # メモリ走査関連定義
    SCAN_STRIDE = 4  # スキャン間隔（構造体の4バイトアライメント想定）
    PAGE_READWRITE = 0x04 # ページ保護属性：PAGE_READWRITE（WinAPI定数）
    MEM_REGION_ALIGN = 0x1000  # メモリページ境界（通常4KB）
    MENU_STRUCT_SIZE = OFFSET_FLOOR + 1  # menu_struct の構造体サイズ（1バイト多めに読んで境界誤差回避）


    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", ctypes.c_ulong),
            ("RegionSize", ctypes.c_size_t),
            ("State", ctypes.c_ulong),
            ("Protect", ctypes.c_ulong),
            ("Type", ctypes.c_ulong),
        ]


    def get_process_handle(process_name, verbose=True):
        """
        指定プロセス名のハンドル（pymem.Pymem）を取得する。
        接続失敗時はNoneを返す。
        """
        try:
            pm = pymem.Pymem(process_name)
            if verbose:
                print(f"✅ プロセス {process_name} に接続成功")
            return pm
        except Exception as e:
            if verbose:
                print(f"❌ プロセス {process_name} に接続できませんでした。")
                print(f"   エラー内容: {e}")
            return None


    def load_menu_tail_hex_from_csv(filename="locked_dir_val_addr.csv"):
        """
        CSVからmenu_tail_hexの値を読み取って返す（バリデーション付き）。
        - 成功: 例 "ECC"（3桁の16進大文字文字列）
        - 失敗: None
        """

        try:
            # 🔧 実行フォルダ（os.getcwd()）から読み取るように修正
            path = os.path.join(os.getcwd(), filename)

            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw_val = row.get("menu_tail_hex", "").strip().upper()
                    if len(raw_val) == 3:
                        try:
                            val_int = int(raw_val, 16)
                            if 0 <= val_int <= 0xFFF:
                                return raw_val
                            else:
                                print(f"⚠️ menu_tail_hex の数値が範囲外です: {raw_val}")
                        except ValueError:
                            print(f"⚠️ menu_tail_hex に無効な文字があります: {raw_val}")
                    else:
                        print(f"⚠️ menu_tail_hex の長さが不正です: '{raw_val}'")
        except Exception as e:
            print(f"⚠️ CSV読み取り失敗: {e}")
        
        return None






    # --- MEM_COMMIT | PAGE_READWRITE 領域の列挙 ---
    def get_valid_regions(pm, mem_commit, page_readwrite):
        regions = []
        address = 0x0
        mem_info = MEMORY_BASIC_INFORMATION()
        mbi_size = ctypes.sizeof(mem_info)
        while address < 0x7FFFFFFFFFFF:
            result = ctypes.windll.kernel32.VirtualQueryEx(
                pm.process_handle, ctypes.c_void_p(address),
                ctypes.byref(mem_info), mbi_size
            )
            if not result:
                address += MEM_REGION_ALIGN
                continue

            base_address = mem_info.BaseAddress
            if base_address is not None:
                addr_val = int(base_address)
                if mem_info.State == mem_commit and mem_info.Protect == page_readwrite:
                    regions.append((addr_val, mem_info.RegionSize))
                address = addr_val + mem_info.RegionSize
            else:
                address += MEM_REGION_ALIGN
        return regions


    def read_regions_bytes(pm, regions, tail_hex=None):
        """
        有効なメモリ領域からデータを読み取り、(base, data) のリストを返す。
        tail_hexが指定されている場合は、アドレスの末尾が一致するもののみ対象とする。
        """
        result = []
        target_tail = int(tail_hex, 16) if tail_hex else None

        for base, size in regions:
            # tail_hexあり → ページ内の末尾一致アドレスだけを読む
            if target_tail is not None:
                for page_base in range(base, base + size, MEM_REGION_ALIGN):
                    addr = page_base + (target_tail & 0xFFF)
                    if addr < base + size:
                        try:
                            data = pm.read_bytes(addr, MENU_STRUCT_SIZE)
                            result.append((addr, data))
                        except Exception as e:
                            print(f"⚠️ tail_hex一致アドレス読み取り失敗: 0x{addr:X} → {e}")
            else:
                # 通常の範囲読み取り（従来通り）
                try:
                    data = pm.read_bytes(base, size)
                    result.append((base, data))
                except Exception as e:
                    print(f"⚠️ メモリ読み取り失敗: base=0x{base:X}, size={size} → {e}")
        return result


    def scan_menu_struct_offsets(
        region_data,
        offset_state=OFFSET_STATE,
        offset_cursor=OFFSET_CURSOR,
        state_val=MENU_STRUCT_STATE,
        cursor_val=MENU_STRUCT_CURSOR,
        stride=SCAN_STRIDE
    ):
        """
        与えられたメモリデータ内から、menu_stateおよびmenu_cursorの一致条件を満たす構造体先頭位置を抽出する。
        """
        matched = []

        for base, data in region_data:
            for i in range(0, len(data) - max(offset_state, offset_cursor), stride):
                try:
                    if data[i + offset_state] == state_val and data[i + offset_cursor] == cursor_val:
                        matched.append((base + i, data, i))
                except IndexError:
                    continue

        return matched
        

    def filter_menu_struct_offsets(
        offsets,
        state_val=MENU_IDLE_STATE,
        cursor_val=MENU_IDLE_CURSOR,
        offset_state=OFFSET_STATE,
        offset_cursor=OFFSET_CURSOR
    ):
        """
        menu_state および menu_cursor の一致を条件にフィルタする。

        Parameters:
            offsets: [(addr, data, i)] のリスト
            state_val: 一致させたい menu_state の値（例: MENU_IDLE_STATE）
            cursor_val: 一致させたい menu_cursor の値（例: MENU_IDLE_CURSOR）
            offset_state: menu_state の相対オフセット
            offset_cursor: menu_cursor の相対オフセット

        Returns:
            [(addr, data, i)] フィルタ通過したリスト
        """
        matched = []
        for addr, data, i in offsets:
            try:
                state = data[i + offset_state]
                cursor = data[i + offset_cursor]
                if state == state_val and cursor == cursor_val:
                    matched.append((addr, data, i))
            except IndexError:
                print(f"⚠️ menu_structフィルタ中に読み取り失敗: 0x{addr:X}")
        print(f"✅ {state_val:02X}/{cursor_val:02X} アドレス数: {len(matched)} 件")
        return matched


    def press_key(key, wait=0.1):
        """
        指定したキーを1回送信し、指定秒数だけ待つ。

        Parameters:
            key: 送信するキー名（例: "l", "esc", "f5" など）
            wait: キー送信後のウェイト秒数（デフォルト0.5）
        """
        try:
            pyautogui.press(key)
            time.sleep(wait)
        except Exception as e:
            print(f"[キー送信失敗] {key} → {e}")

    def unlock_wizardry(title=WINDOW_TITLE):
        hwnd = win32gui.FindWindow(None, title)
        if hwnd and win32gui.IsWindow(hwnd):
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.1)

                if not win32gui.IsWindowEnabled(hwnd):
                    win32gui.EnableWindow(hwnd, True)
                time.sleep(0.1)

                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.1)
                win32gui.SetActiveWindow(hwnd)
                print(f"✅ {title} を前面に出しました")
            except Exception as e:
                print(f"❌ {title} のフォーカス失敗: {e}")
        else:
            print(f"❌ {title} のウィンドウが見つかりません")



    def lock_wizardry(title=WINDOW_TITLE):
        hwnd = win32gui.FindWindow(None, title)
        if hwnd and win32gui.IsWindow(hwnd):
            win32gui.EnableWindow(hwnd, False)
            print(f"🔒 {title} をロックしました")
        else:
            print(f"❌ {title} のロック失敗")



    def lock_and_output(menu_struct_entries):
        """
        最終確定したDIR構造体（menu_struct）候補をCSV出力する。
        現在の実装では1件のみを想定。複数件ある場合はログ出力して終了。
        """

        if len(menu_struct_entries) == 1:
            addr, _, _ = menu_struct_entries[0]
            menu_state_addr = addr + OFFSET_STATE      # +0x00
            dir_val_addr = addr + OFFSET_DIR           # +0x4C
            menu_tail_hex = f"{menu_state_addr & 0xFFF:03X}"  # 下位3桁を抽出

            print(f"🎯 DIR構造体のdir_valアドレスを特定: 0x{dir_val_addr:X} （MENU_STATE末尾3桁: {menu_tail_hex}）")

            try:
                # ✅ グローバル関数を使ってEXEルートに保存
                path = os.path.join(get_base_path_for_data(), "locked_dir_val_addr.csv")

                with open(path, "w", encoding="utf-8") as f:
                    f.write("menu_state_addr,dir_val_addr,menu_tail_hex\n")
                    f.write(f"0x{menu_state_addr:X},0x{dir_val_addr:X},{menu_tail_hex}\n")

                print(f"📝 CSV出力完了 → {path}")

            except Exception as e:
                print(f"❌ CSV出力に失敗しました: {e}")

        else:
            print(f"❌ 候補が {len(menu_struct_entries)} 件。特定できません。")
            for addr, _, _ in menu_struct_entries:
                print(f"  📍 候補アドレス: 0x{addr:X}")




    def _scan_dir_struct_with_tail(pm, regions, tail_hex=None):
        """
        tail_hex（末尾フィルタ）の有無にかかわらず、
        WIZ状態遷移→スキャン→整合フィルタ（C8/0）までを一括で行う共通関数。

        Parameters:
            pm: pymem.Pymem オブジェクト
            regions: 有効メモリ領域リスト（get_valid_regionsの結果）
            tail_hex: "ECC" 等の下位3桁16進文字列（Noneなら全スキャン）

        Returns:
            c8_offsets: [(addr, data, offset)] 形式の候補リスト
        """

        # === 🧭 状態操作フェーズ（D2/0B） ===

        unlock_wizardry()
        for _ in range(4): press_key("l")
        press_key("esc")
        press_key("w")
        time.sleep(0.2)
        lock_wizardry()

        # === 💾 メモリ読み込みフェーズ（D2/0B） ===

        region_data = read_regions_bytes(pm, regions, tail_hex=tail_hex)
        print(f"📥 メモリ領域の読み取り完了（{len(region_data)}件）")

        t0 = time.time()
        offsets = scan_menu_struct_offsets(
            region_data,
            offset_state=OFFSET_STATE,
            offset_cursor=OFFSET_CURSOR,
            state_val=MENU_STRUCT_STATE,
            cursor_val=MENU_STRUCT_CURSOR,
            stride=SCAN_STRIDE
        )
        print(f"🎯 候補アドレス数: {len(offsets)} 件（スキャン時間: {time.time() - t0:.2f}秒）")

        # === 🔁 C8状態へ遷移し整合確認 ===

        unlock_wizardry()
        press_key("l")
        time.sleep(0.2)
        lock_wizardry()

        refreshed_offsets = []
        for addr, _, _ in offsets:
            try:
                data = pm.read_bytes(addr, MENU_STRUCT_SIZE)
                refreshed_offsets.append((addr, data, 0))
            except Exception as e:
                print(f"⚠️ 再読込失敗: 0x{addr:X} → {e}")

        c8_offsets = filter_menu_struct_offsets(
            refreshed_offsets,
            state_val=MENU_IDLE_STATE,
            cursor_val=MENU_IDLE_CURSOR,
            offset_state=OFFSET_STATE,
            offset_cursor=OFFSET_CURSOR
        )
        unlock_wizardry()
        print(f"✅ C8/0アドレス数: {len(c8_offsets)} 件")

        return c8_offsets


    def find_locked_dir_struct_candidates(pm):
        """
        DIR構造体候補を特定する高レベル関数。
        - まずは menu_tail_hex によるピンポイントスキャンを試み、
        - 一致0件なら tail_hex を無効化して全スキャンにフォールバック。

        Returns:
            c8_offsets: [(addr, data, offset)] 一致した構造体のリスト
        """

        regions = get_valid_regions(pm, MEM_REGION_ALIGN, PAGE_READWRITE)
        print(f"📊 有効メモリ領域数: {len(regions)}")

        tail_hex = load_menu_tail_hex_from_csv()
        print(f"🔍 tail_hexフィルタ使用: {'あり → ' + tail_hex if tail_hex else 'なし'}")
        c8_offsets = _scan_dir_struct_with_tail(pm, regions, tail_hex=tail_hex)

        if not c8_offsets and tail_hex:
            print(f"🔁 tail_hex={tail_hex} による一致なし → フォールバックで全域スキャン実行")
            c8_offsets = _scan_dir_struct_with_tail(pm, regions, tail_hex=None)

            if not c8_offsets:
                print("❌ フォールバック後も一致なし。処理を終了します。")

        return c8_offsets



    def main():
        pm = get_process_handle(process_name=WINDOW_TITLE)
        if pm is None:
            return

        print("🔍 DIR構造体サーチ開始…")
        c8_offsets = find_locked_dir_struct_candidates(pm)

        if not c8_offsets:
            print("❌ 一致するアドレスがありませんでした。")
            return

        if len(c8_offsets) == 1:
            addr, data, i = c8_offsets[0]
            dir_val_addr = addr + OFFSET_DIR
            print(f"🔒 dir_val_addr: 0x{dir_val_addr:X}")
            lock_and_output(c8_offsets)
        else:
            print("⚠️ 一意に決まらなかったので候補を表示:")
            for addr, _, _ in c8_offsets:
                print(f"  📍 0x{addr:X}")

    main()

# ================================
# 📦 DIRスキャナ機能 ここまで
# ================================





WINDOW_TITLE = "WizardryFoV2"

def get_base_path():
    if getattr(sys, 'frozen', False):  # exe化されている場合
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def get_base_path_for_data():
    """
    実行ファイル（.exe）本体が存在するフォルダを返す。
    - EXE化されている場合でも、Tempフォルダではなく本体位置を返す
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))



# === 設定ファイルパス ===
SETTINGS_FILE = os.path.join(get_base_path_for_data(), "last_scenario.txt")
LANG_FILE = os.path.join(get_base_path_for_data(), "lang.txt")




# --- 説明 ---
# 前回選択されたシナリオ名をファイルから読み取る（存在しなければ None を返す）
def load_last_selected_scenario():
    try:
        if not os.path.exists(SETTINGS_FILE):
            return None
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            name = f.read().strip()
            if not name:
                return None
            return name
    except Exception as e:
        print(f"📛 シナリオ設定読み込み失敗: {e}")
        return None

# --- 説明 ---
# 現在選択されているシナリオ名を外部ファイルに保存する（次回起動時に再現できるように）
def save_last_selected_scenario(name):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write(name.strip())
    except Exception as e:
        print(f"📛 シナリオ設定保存失敗: {e}")


# --- 説明 ---
# 現在選択されているシナリオ名に応じたマップ画像保存先のパスを返す。
# 保存先フォルダが存在しない場合は自動作成する。
def get_scenario_save_path(scenario_name):
    base = os.path.join(get_base_path_for_data(), "map_images") 

    try:
        os.makedirs(base, exist_ok=True)  # ✅ map_images フォルダ保証
    except Exception as e:
        print(f"📛 map_images フォルダ作成失敗: {e}")
        return None

    if not scenario_name:
        return base

    folder = os.path.join(base, scenario_name)
    try:
        os.makedirs(folder, exist_ok=True)  # ✅ シナリオ個別フォルダも保証
    except Exception as e:
        print(f"📛 シナリオフォルダ作成失敗: {e}")
        return None

    return folder


def prompt_select_resolution(parent_root): #定義のみで未使用
    """
    解像度自動検出に失敗した場合、解像度プロファイルをユーザーに選ばせる。
    tkinter.simpledialog による簡易ポップアップ。
    
    Parameters:
        parent_root: すでに生成済みの Tk root ウィンドウを親として使用。
    """
    from tkinter import simpledialog

    options = list(RESOLUTION_PROFILES.keys())
    selected = simpledialog.askstring(
        get_ui_lang("resolution_prompt_title"),
        get_ui_lang("resolution_prompt_msg", options=options),
        parent=parent_root
    )

    if selected in RESOLUTION_PROFILES:
        print(f"✅ 手動で選択された解像度: {selected}")
        return selected
    else:
        print(f"⚠️ 不正な入力またはキャンセル → fallback: 720p")
        return "720p"



# --- 使用中のメニュー状態テーブル（menu_state値の定義と表示用） ---
MENU_STATE_TABLE = {
    "map_open": [
        {"addr": 0x25B, "label": "マップ表示中（探索経由）", "type": "ui"},# MAPVIEWERトリガー
        {"addr": 0xB4,  "label": "マップ表示中（キャラ調査経由）", "type": "ui"},#キャラ調査・呪文を唱える経由
    ]
}
MAP_MENU_STATES = {entry["addr"] for entry in MENU_STATE_TABLE["map_open"]}

MAX_SCENARIO_LENGTH = 16  # 半角換算（全角なら8文字相当）

LANG_DICT = {
    "window_title": {
        "ja": "Wiz Codex: Mapbook",
        "en": "Wiz Codex: Mapbook"
    },
    "dir_names": {
    "ja": ["北", "東", "南", "西"],
    "en": ["North", "East", "South", "West"]
    },
    "label_dir_xy_default": {
        "ja": "向き: -　X: -　Y: -",
        "en": "Dir: -   X: -   Y: -"
    },
    "label_dir_xy": {
    "ja": "向き: {dir}　X: {x}　Y: {y}",
    "en": "Dir: {dir}   X: {x}   Y: {y}"
    },
    "floor_label_loading": {
        "ja": "現在フロア: 読込中...",
        "en": "Current Floor: Loading..."
    },
    "floor_label_fmt": {
        "ja": "現在フロア: {floor}F",
        "en": "Current Floor: {floor}F"
    },
    "floor_label_outside": {
        "ja": "現在フロア: ダンジョン外",
        "en": "Outside the Dungeon"
    },
    "scenario_label": {
        "ja": "シナリオ：",
        "en": "Scenario:"
    },
    "frame_ops": {
        "ja": "🛠 操作メニュー",
        "en": "🛠 Operations"
    },
    "btn_open_folder": {
        "ja": "📂",
        "en": "📂"
    },
    "btn_add_scenario": {
        "ja": "➕",
        "en": "➕"
    },
    "btn_capture": {
        "ja": "📸 現在のマップを保存",
        "en": "📸 Capture current map"
    },
    "btn_rescan": {
        "ja": "🔄 状態を再取得（探索中のみ）",
        "en": "🔄 Rescan (dungeon only)"
    },
    "chk_auto_capture": {
        "ja": "🗺 マップ自動保存",
        "en": "🗺 Enable Auto Map Capture"
    },
    "chk_topmost": {
        "ja": "🔝 常に最前面表示",
        "en": "🔝 Always on Top"
    },
    "status_idle": {
        "ja": "",
        "en": ""
    },
    "capture_notice_fmt": {
        "ja": "🖼 保存完了: {filename}",
        "en": "🖼 Saved: {filename}"
    },
    "resolution_prompt_title": {
    "ja": "解像度選択",
    "en": "Select Resolution"
    },
    "resolution_prompt_msg": {
    "ja": "自動検出に失敗しました。\n以下のいずれかを入力してください：\n{options}",
    "en": "Auto-detection failed.\nPlease enter one of the following:\n{options}"
    },
    "resolution_btn_fmt": {
    "ja": "🖥 解像度: {res}（再取得）",
    "en": "🖥 Resolution: {res} (Update)"
},
    "chk_show_minimap": {
        "ja": "🗺 ミニマップ表示",
        "en": "🗺 Show minimap"
    },
    "title_minimap": {
        "ja": "ミニマップ表示",
        "en": "Minimap View"
    },
    "error_title": {
        "ja": "エラー",
        "en": "No Response"
    },
    "error_addr_load_failed": {
        "ja": "アドレスの読み込みに失敗しました。\nダンジョン内（探索中）に実行してください。",
        "en": "No data found.\nIt reveals itself only during dungeon exploration."
    },
    "warn_invalid_name": {
        "ja": "使用できない文字が含まれています: <>:\"/\\|?*",
        "en": "The name contains invalid characters: <>:\"/\\|?*"
    },
    "error_rescan_failed": {
    "ja": "構造体の再スキャンに失敗しました。",
    "en": "Failed to rescan the structure."
    },
    "error_create_failed": {
        "ja": "新規作成に失敗しました。",
        "en": "Failed to create new entry."
    },
    "error_select_first": {
        "ja": "先に項目を選択してください。",
        "en": "Please select an item first."
    },
    "warning_input": {
    "ja": "入力エラー",
    "en": "Input Error"
    },
    "warn_scenario_name_too_long": {
        "ja": "シナリオ名は最大{max}文字以内で入力してください。",
        "en": "Scenario name must be within {max} characters."
    },
    "warn_invalid_chars": {
    "ja": "使用できない文字が含まれています: <>:\"/\\|?*",
    "en": "The name contains invalid characters: <>:\"/\\|?*"
    },
    "prompt_create_title": {
        "ja": "新規シナリオ作成",
        "en": "Create New Scenario"
    },
    "prompt_create_text": {
        "ja": "シナリオ名を入力してください",
        "en": "Enter a name for your scenario."
    },
    "warn_scenario_name_exists": {
    "ja": "シナリオ「{name}」はすでに存在します",
    "en": "A scenario named \"{name}\" already exists."
    },
    "error_create_failed_title": {
    "ja": "作成失敗",
    "en": "Creation Failed"
    },
    "error_create_failed_detail": {
    "ja": "フォルダの作成に失敗しました:\n{error}",
    "en": "Failed to create folder:\n{error}"
    },
    "error_open_folder_failed": {
    "ja": "フォルダを開けませんでした:\n{error}",
    "en": "Failed to open folder:\n{error}"
    },

}


def load_lang():
    try:
        with open(LANG_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return "en"

CURRENT_LANG = load_lang()

def save_lang():
    try:
        with open(LANG_FILE, "w", encoding="utf-8") as f:
            f.write(CURRENT_LANG)
    except:
        pass

def toggle_language(self):
    global CURRENT_LANG
    CURRENT_LANG = "ja" if CURRENT_LANG == "en" else "en"
    save_lang()
    self.btn_lang_toggle.config(text=f"🌐 : {CURRENT_LANG.upper()}")
    self.refresh_ui_language()





def get_ui_lang(key, **kwargs):
    """
    LANG_DICTから現在言語のUI文言を取得する関数。
    - 存在しないキーや言語設定には "[key]" を返す
    - kwargsにより .format() 対応も可能

    Parameters:
        key (str): LANG_DICT のキー
        kwargs: .format() 用の引数（例: name, error）
    """
    try:
        text = LANG_DICT[key][CURRENT_LANG]
    except KeyError:
        return f"[{key}]"
    return text.format(**kwargs) if kwargs else text


# --- エラー表示共通関数（常に前面に表示） ---
def show_ui_error(title_key, message_key, parent=None, **kwargs):
    """
    多言語対応のエラーメッセージボックスを表示する。
    ウィンドウの前面に出すために lift + topmost を使用。

    Parameters:
        title_key: LANG_DICT のタイトルキー
        message_key: LANG_DICT の本文キー
        parent: 親ウィンドウ（Tk または Toplevel）
    """
    if parent is not None:
        parent.lift()
        parent.attributes("-topmost", True)              # 👈 追加推奨
        parent.after(10, lambda: parent.attributes("-topmost", False))  # 👈 一時的だけ有効
        parent.focus_force()

    messagebox.showerror(
        get_ui_lang(title_key),
        get_ui_lang(message_key, **kwargs),
        parent=parent
    )



def show_ui_warning(title_key, message_key, parent=None, **kwargs):
    """
    多言語対応の警告メッセージボックスを表示する。
    """
    if parent is not None:
        parent.lift()
        parent.focus_force()
    messagebox.showwarning(
        get_ui_lang(title_key),
        get_ui_lang(message_key, **kwargs),
        parent=parent
    )



# --- 説明 ---
# DIR構造体の基底アドレスを受け取り、各種要素のアドレスを算出・保持する
class DirStruct:
    OFFSET_DIR = 0x00
    OFFSET_X = 0x04
    OFFSET_Y = 0x08
    OFFSET_FLOOR = 0x0C
    OFFSET_DUNGEON_ID = 0x18
    OFFSET_MENU_STATE = -0x4C
    

    def __init__(self, handle, base_addr):
        self.handle = handle
        self.base = base_addr
        


        # 構造体内の各要素のオフセットを加算し、個別アドレスとして保持
        self.addr_dir = base_addr + self.OFFSET_DIR
        self.addr_x = base_addr + self.OFFSET_X
        self.addr_y = base_addr + self.OFFSET_Y
        self.addr_floor = base_addr + self.OFFSET_FLOOR
        self.addr_dungeon_id = base_addr + self.OFFSET_DUNGEON_ID
        self.addr_menu_state = base_addr + self.OFFSET_MENU_STATE

    def read_dir(self):
        return read_int(self.handle, self.addr_dir)

    def read_x(self):
        return read_int(self.handle, self.addr_x)

    def read_y(self):
        return read_int(self.handle, self.addr_y)

    def read_floor(self):
        return read_int(self.handle, self.addr_floor)

    def read_dungeon_id(self):
        return read_int(self.handle, self.addr_dungeon_id)

    @property
    def all_values(self):
        return {
            "dir": self.read_dir(),
            "x": self.read_x(),
            "y": self.read_y(),
            "floor": self.read_floor(),
            "dungeon_id": self.read_dungeon_id(),
        }


# ====== メモリ読み取り ======
def get_process_handle(title):
    hwnd = win32gui.FindWindow(None, title)
    if hwnd == 0:
        raise Exception("ウィンドウが見つかりません")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    PROCESS_VM_READ = 0x10
    PROCESS_QUERY_INFORMATION = 0x400
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        raise Exception("プロセスのオープンに失敗しました")
    return handle


def get_window_resolution():
    """
    Wizardry ウィンドウのクライアント領域サイズ（幅・高さ）を取得。
    最小化されているなどで取得不能な場合は None を返す。
    """
    hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
    if hwnd == 0:
        print("⚠️ ゲームウィンドウが見つかりません")
        return None

    rect = win32gui.GetClientRect(hwnd)
    width, height = rect[2] - rect[0], rect[3] - rect[1]

    if width == 0 or height == 0:
        print("⚠️ クライアント領域サイズが 0x0 → 最小化中 or 非表示")
        return None

    print(f"🪟 ゲームウィンドウ解像度: {width}x{height}")
    return width, height




def ensure_wizardry_visible():
    """
    Wizardry のウィンドウが最小化されている場合に復元し、
    アクティブ状態にして入力を受け付けるようにする。
    """
    hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
    if hwnd:
        win32gui.EnableWindow(hwnd, True)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # 最小化解除
        time.sleep(0.1)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetActiveWindow(hwnd)
        time.sleep(0.3)



def read_int(handle, address):
    buffer = ctypes.create_string_buffer(4)
    bytesRead = ctypes.c_size_t()
    success = ctypes.windll.kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        4,
        ctypes.byref(bytesRead)
    )
    if not success:
        return None
    return struct.unpack("i", buffer.raw)[0]


# --- 説明 ---
# locked_dir_val_addr.csv から dir_val_addr を読み取り、16進数として整数変換して返す
def load_auto_dir_address():
    import os, csv

    try:
        path = os.path.join(get_base_path_for_data(), "locked_dir_val_addr.csv")
        if not os.path.exists(path):
            return None  # ファイルが存在しない場合は None を返す（初回起動想定）

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "dir_val_addr" in row and row["dir_val_addr"]:
                    hex_str = row["dir_val_addr"].strip().lower().replace("0x", "")
                    return int(hex_str, 16)
                break  # 最初の有効な行だけ処理する

    except Exception as e:
        print(f"📛 自動DIRアドレス読み込み失敗: {e}")
        return None




# --- 説明 ---
# 指定されたシナリオ名に対応するフォルダ内から "_full.png" 形式のファイルをスキャンし、
# フロア番号をキーとした辞書を返す。画像が壊れていても処理を継続する。
def find_floor_maps(scenario_name):

    folder = get_scenario_save_path(scenario_name)
    if folder is None:
        print("📛 get_scenario_save_path() が None を返しました")
        return {}

    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        print(f"📛 os.makedirs() 失敗: {e}")
        return {}

    images = {}
    try:
        for filename in os.listdir(folder):
            if filename.endswith("_full.png"):
                match = re.fullmatch(r"map_(\d+)f_full\.png", filename, re.IGNORECASE)
                if match:
                    try:
                        floor = int(match.group(1))
                        images[floor] = os.path.join(folder, filename)
                    except Exception:
                        continue
    except Exception as e:
        print(f"📛 os.listdir() 失敗: {e}")
        return {}

    return dict(sorted(images.items()))



# --- クロップ領域クラス定義（解像度別の画面切り出し範囲） ---
class CropArea:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def as_tuple(self):
        return (self.left, self.top, self.right, self.bottom)
    
    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top
    

# --- 解像度別クロップ定義 ---
CROP_PRESETS = {
    "576p": CropArea(372, 120, 650, 398),     # 自動生成
    "720p": CropArea(465, 150, 813, 498),     # 手動定義
    "768p": CropArea(496, 160, 868, 531),     # 自動生成
    "900p": CropArea(581, 188, 1016, 622),    # 自動生成
    "1080p": CropArea(698, 225, 1220, 747),   # 自動生成
    "1152p": CropArea(744, 240, 1301, 797),   # 自動生成
    "1440p": CropArea(930, 300, 1628, 998),   # 自動生成
    "2160p": CropArea(1395, 450, 2442, 1497)  # 自動生成
}

# --- 解像度別プロファイル構造 ---
class ResolutionProfile:
    def __init__(self, crop, cell_size, map_origin_x, map_origin_y, marker_size):
        self.map_crop = crop
        self.cell_size = cell_size
        self.map_origin_x = map_origin_x
        self.map_origin_y = map_origin_y
        self.marker_size = marker_size


# --- 解像度別プロファイル定義 ---
RESOLUTION_PROFILES = {
    "576p": ResolutionProfile(
        crop=CROP_PRESETS["576p"],
        cell_size=12.80,
        map_origin_x=396,
        map_origin_y=374,
        marker_size=5  # 自動生成
    ),
    "720p": ResolutionProfile(
        crop=CROP_PRESETS["720p"],
        cell_size=16.0,
        map_origin_x=495,
        map_origin_y=468,
        marker_size=6  # 手動定義
    ),
    "768p": ResolutionProfile(
        crop=CROP_PRESETS["768p"],
        cell_size=17.07,
        map_origin_x=528,
        map_origin_y=499,
        marker_size=6  # 自動生成
    ),
    "900p": ResolutionProfile(
        crop=CROP_PRESETS["900p"],
        cell_size=20.00,
        map_origin_x=619,
        map_origin_y=585,
        marker_size=8  # 自動生成
    ),
    "1080p": ResolutionProfile(
        crop=CROP_PRESETS["1080p"],
        cell_size=24.00,
        map_origin_x=742,
        map_origin_y=702,
        marker_size=9  # 自動生成
    ),
    "1152p": ResolutionProfile(
        crop=CROP_PRESETS["1152p"],
        cell_size=25.60,
        map_origin_x=792,
        map_origin_y=749,
        marker_size=10  # 自動生成
    ),
    "1440p": ResolutionProfile(
        crop=CROP_PRESETS["1440p"],
        cell_size=32.00,
        map_origin_x=990,
        map_origin_y=936,
        marker_size=12  # 自動生成
    ),
    "2160p": ResolutionProfile(
        crop=CROP_PRESETS["2160p"],
        cell_size=48.00,
        map_origin_x=1485,
        map_origin_y=1404,
        marker_size=18  # 自動生成
    )
}


def get_and_select_resolution():
    """
    ゲームウィンドウのクライアントサイズから解像度キーを決定する。
    - 取得失敗時は "720p" にフォールバック
    - 未登録キーも "720p" にフォールバック
    """
    res = get_window_resolution()
    if res is None:
        print("⚠️ 解像度取得失敗 → fallback: 720p")
        return "720p"

    w, h = res
    res_key = f"{h}p"

    if res_key in RESOLUTION_PROFILES:
        print(f"✅ 解像度認識: {w}x{h} → プロファイル: {res_key}")
        return res_key
    else:
        print(f"⚠️ 未登録解像度: {w}x{h} → fallback: 720p")
        return "720p"




# ====== GUIテーマ関連 ======

# 🔧 現在の適用テーマ関数（ここを差し替えるだけで変更可能！）
CURRENT_THEME = None # ここでは指定禁止　下部CURRENT_THEME = で指定

def apply_theme_default(root):
    """デフォルトテーマ：何もしない（将来の互換用）"""
    pass

def apply_theme_retro(root):
    """レトロPC風テーマ：黒背景＋緑文字＋等幅フォント"""
    def walk(w):
        for child in w.winfo_children():# 子ウィジェットを再帰的に処理
            walk(child)
        if isinstance(w, (tk.Label, tk.Button, tk.Checkbutton)):
            w.config(bg="black", fg="#00FF00", font=("Courier New", 11, "bold")) # テキスト系：黒背景＋緑文字＋等幅フォント
        elif isinstance(w, (tk.Frame, tk.Tk)):
            w.config(bg="black") # フレーム類：黒背景に統一
        elif isinstance(w, tk.LabelFrame):
            w.config(bg="black", fg="#00FF00")  # 枠付きラベル：背景＋文字色を統一
        elif isinstance(w, tk.Canvas):
            w.config(bg="black")   # Canvas背景：黒で埋める（画像がない時も自然）
    
        # ✅ 最下部余白削除調整　調整しちゃダメだった LABELいた
    #reduce_size = 30
    #root.update_idletasks()
    #w = root.winfo_width()
    #h = root.winfo_height()
    #if h > reduce_size: 
    #   root.geometry(f"{w}x{h - reduce_size}")
    #else:
    #    print(f"[apply_theme_retro] ⚠️ 高さが小さすぎて調整できませんでした（h={h}, 減少予定={reduce_size}）")

    walk(root) # ルートから再帰的にテーマ適用

def apply_theme_wizard_terminal(root):
    """魔導書風テーマ：黒背景＋琥珀色フォント＋クラシカル"""
    def walk(w):
        for child in w.winfo_children():
            walk(child)
        if isinstance(w, (tk.Label, tk.Button, tk.Checkbutton)):
            w.config(bg="black", fg="#ffc857", font=("Courier New", 11, "bold"))
        elif isinstance(w, (tk.Frame, tk.Tk, tk.LabelFrame)):
            w.config(bg="black",) 
        elif isinstance(w, tk.Canvas):
            w.config(bg="black")
    walk(root)

def apply_theme_codex_dark(root):
    """現代Wiz風テーマ：超濃いグレー背景＋くすみ白＋ミントアクセント"""
    def walk(w):
        for child in w.winfo_children():
            walk(child)
        if isinstance(w, tk.Checkbutton):
            w.config(bg="#1a1a1a", fg="#e0e0e0", selectcolor="#1a1a1a", font=("Arial", 11)
            )
        if isinstance(w, (tk.Label, tk.Button)):
            w.config(bg="#1a1a1a", fg="#e0e0e0", font=("Arial", 11))  # Arialで中立的に
        elif isinstance(w, (tk.Frame, tk.Tk, tk.LabelFrame)):
            w.config(bg="#1a1a1a")
        elif isinstance(w, tk.Canvas):
            w.config(bg="#1a1a1a")
    walk(root)



CURRENT_THEME = apply_theme_codex_dark # ✅ 初期テーマをここで指定



# ====== マップ表示クラス ======
class MapApp:


    def __init__(self, root, handle, addr_dir):
        self.root = root  # GUIの司令塔。一括制御用に保持
        self.handle = handle
        self.addr_dir_base = addr_dir
        self.dir_struct = DirStruct(self.handle, addr_dir) if addr_dir is not None else None
        self.capturing = False
        self.minimap_window = None  # 切り替え先ウィンドウ用

        # --- 解像度プロファイル読み込み（差し替え）---
        self.update_resolution_profile()

        # --- ミニマップ更新（自己再帰）---
        self.tick_mini_map()


        # 🔽 既存シナリオ一覧を取得
        scenario_base = os.path.join(get_base_path(), "map_images")
        try:
            os.makedirs(scenario_base, exist_ok=True)
        except Exception as e:
            print(f"📛 map_images フォルダ作成失敗: {e}")
            scenario_list = []
        else:
            scenario_list = [name for name in os.listdir(scenario_base)
                            if os.path.isdir(os.path.join(scenario_base, name))]

        # 📂 最後に選択されていたシナリオ名を取得（なければ最初の1件）
        last_selected = load_last_selected_scenario()
        self.selected_scenario = last_selected if last_selected in scenario_list else (scenario_list[0] if scenario_list else "")

        # --- フロア画像とキャッシュの初期化 ---
        self.map_images = find_floor_maps(self.selected_scenario)
        self.current_floor = min(self.map_images.keys()) if self.map_images else 1
        self.image_cache = {}

        # ===============================
        # 📦 キャンバス構築（マップ＋ポチ）
        # ===============================
        first_img = self.load_map_image(self.current_floor)
        self.tk_img = ImageTk.PhotoImage(first_img)
        self.canvas = tk.Canvas(root, width=self.map_crop.width(), height=self.map_crop.height(), highlightthickness=1)
        self.canvas.pack(expand=True, fill="both", pady=(5, 0))
        self.canvas_img_id = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
        self.canvas.image = self.tk_img  # 参照を保持してGCを防ぐ

        self.marker = self.canvas.create_polygon(0, 0, 0, 0, 0, 0, fill="red")
        self.dir_text = self.canvas.create_text(10, 10, anchor=tk.NW, fill="white", font=("Arial", 12))

        self.label_dir_xy = tk.Label(root, text=get_ui_lang("label_dir_xy_default"), font=("Arial", 11))
        self.label_dir_xy.pack()

        self.floor_label = tk.Label(root, text=get_ui_lang("floor_label_loading"), font=("Arial", 12))
        self.floor_label.pack()

        self.button_frame = tk.Frame(root)
        self.button_frame.pack()
        self.refresh_floor_buttons()

        # ===============================
        # 📦 操作エリア（frame_controls）
        # ===============================
        frame_controls = tk.Frame(root)
        frame_controls.pack(pady=(10, 0))

        # --- 🗂 シナリオバー（選択・追加・フォルダ表示） ---
        self.scenario_row = tk.Frame(frame_controls)
        self.scenario_row.pack(anchor="w", padx=10, pady=(0, 5))

        label_scenario = tk.Label(self.scenario_row, text=get_ui_lang("scenario_label"))
        label_scenario.pack(side=tk.LEFT)

        self.combo_scenario = ttk.Combobox(self.scenario_row, values=scenario_list, state="readonly", width=20)
        self.combo_scenario.set(self.selected_scenario)
        self.combo_scenario.pack(side=tk.LEFT)
        self.combo_scenario.bind("<MouseWheel>", lambda e: "break")

        self.btn_open_folder = tk.Button(self.scenario_row, text=get_ui_lang("btn_open_folder"), width=3, command=self.open_scenario_folder)
        self.btn_open_folder.pack(side=tk.LEFT, padx=(5, 0))

        self.btn_add_scenario = tk.Button(self.scenario_row, text=get_ui_lang("btn_add_scenario"), width=3, command=self.on_add_scenario)
        self.btn_add_scenario.pack(side=tk.LEFT, padx=(5, 0))

        self.combo_scenario.bind("<<ComboboxSelected>>", self.on_select_scenario)

        # --- 🛠 操作メニュー ---
        frame_ops = tk.LabelFrame(frame_controls,  padx=10, pady=5) #背景と文字が同化するからいったん削除text=get_ui_lang("frame_ops")
        frame_ops.pack(fill=tk.X, padx=10, pady=(0,10))

        self.btn_capture = tk.Button(frame_ops, text=get_ui_lang("btn_capture"), command=self.capture_map_screenshot)
        self.btn_capture.pack(anchor="w", pady=2)

        self.btn_update_resolution = tk.Button(
            frame_ops,
            text=get_ui_lang("resolution_btn_fmt", res=self.current_res_key),
            command=self.on_click_update_resolution,
            
        )
        self.btn_update_resolution.config(
            text=get_ui_lang("resolution_btn_fmt", res=self.current_res_key)
        )
        self.btn_update_resolution.pack(anchor="w", pady=(2))

        self.btn_rescan = tk.Button(
            frame_ops,
            text=get_ui_lang("btn_rescan"),
            command=lambda: self.rescan_and_reload()
        )
        self.btn_rescan.pack(anchor="w", pady=2)

        self.auto_capture_enabled = tk.BooleanVar(value=False)
        self.chk_auto_capture = tk.Checkbutton(
            frame_ops,
            text=get_ui_lang("chk_auto_capture"),
            variable=self.auto_capture_enabled
        )
        self.chk_auto_capture.pack(anchor="w", pady=2)

        self.topmost_var = tk.BooleanVar(value=False)
        self.chk_topmost = tk.Checkbutton(
            frame_ops,
            text=get_ui_lang("chk_topmost"),
            variable=self.topmost_var,
            command=self.toggle_topmost_window
        )
        self.chk_topmost.pack(anchor="w", pady=2)


        # ミニマップ表示トグル
        self.minimap_var = tk.BooleanVar(value=False)
        # ミニマップ制御用変数
        
        self.chk_show_minimap = tk.Checkbutton(
            frame_ops,
            text=get_ui_lang("chk_show_minimap"),
            variable=self.minimap_var,
            command=self.on_toggle_minimap
        )
        self.chk_show_minimap.pack(anchor="w", pady=2)

        # --- 🌐 言語切り替えボタン ---
        self.btn_lang_toggle = tk.Button(
            frame_ops,
            text=f"🌐: {CURRENT_LANG.upper()}",
            command=self.toggle_language,
            font=("Arial", ),
            width=6,
            relief=tk.RIDGE
        )
        self.btn_lang_toggle.pack(anchor="e", pady=(2, 0))



        # --- 👇 状態表示に使える汎用メッセージラベル（将来復活用） ---
        # self.status_label = tk.Label(frame_controls, text="", fg="green", font=("Arial", 10))
        # self.status_label.pack(anchor="w", pady=(5, 0))
        # self.label_capture_notice = tk.Label(frame_controls, text="", fg="green", font=("Arial", 10))
        # self.label_capture_notice.pack(anchor="w", pady=(10, 0))
            
        # --- テーマ適用 ---
        CURRENT_THEME(root)

        # --- アイコン適用 ---
        self.set_window_icon()

        # --- 定期更新処理を開始 ---
        self.tick_map_overlay()

    def toggle_language(self):
        global CURRENT_LANG
        CURRENT_LANG = "ja" if CURRENT_LANG == "en" else "en"
        save_lang() 
        self.btn_lang_toggle.config(text=f"🌐 : {CURRENT_LANG.upper()}")
        self.refresh_ui_language()
    

    def refresh_ui_language(self):
        self.btn_capture.config(text=get_ui_lang("btn_capture"))
        self.btn_update_resolution.config(text=get_ui_lang("resolution_btn_fmt", res=self.current_res_key))
        self.btn_rescan.config(text=get_ui_lang("btn_rescan"))
        self.chk_auto_capture.config(text=get_ui_lang("chk_auto_capture"))
        self.chk_topmost.config(text=get_ui_lang("chk_topmost"))
        self.chk_show_minimap.config(text=get_ui_lang("chk_show_minimap"))
        self.btn_open_folder.config(text=get_ui_lang("btn_open_folder"))
        self.btn_add_scenario.config(text=get_ui_lang("btn_add_scenario"))
        self.btn_lang_toggle.config(text=f"🌐 : {CURRENT_LANG.upper()}")


    def update_resolution_profile(self):
        """
        現在のゲームウィンドウ解像度に基づいてプロファイルを再設定する。
        解像度取得に失敗した場合は "720p" にフォールバック。
        """
        res_key = get_and_select_resolution()

        if res_key not in RESOLUTION_PROFILES:
            print(f"⚠️ 不明な解像度キー: {res_key} → fallback: 720p")
            res_key = "720p"

        self.profile = RESOLUTION_PROFILES[res_key]
        self.map_crop = self.profile.map_crop
        self.current_res_key = res_key
        print(f"📦 解像度プロファイルを設定: {res_key}")

    def on_click_update_resolution(self):
        self.update_resolution_profile()
        self.reload_map_image() 
        self.btn_update_resolution.config(
            text=get_ui_lang("resolution_btn_fmt", res=self.current_res_key)
        )





    # --- 新規シナリオ作成処理 ---
    # シナリオ作成ボタンが押された際に呼び出される。
    # 入力はダイアログで受け取り、文字数制限や使用禁止文字を検査。
    # 正常であれば map_images/ 以下にフォルダを作成し、ComboBoxに即追加・選択状態にする。
    def on_add_scenario(self):
        import tkinter.simpledialog

        

        name = tkinter.simpledialog.askstring(
            get_ui_lang("prompt_create_title"),
            get_ui_lang("prompt_create_text")
        )

        if not name:
            return

        name = name.strip()
        if len(name) > MAX_SCENARIO_LENGTH:
            show_ui_warning("warning_input", "warn_scenario_name_too_long", parent=self.root, max=MAX_SCENARIO_LENGTH)
            return
        if any(c in name for c in r'<>:"/\|?*'):
            show_ui_warning("warning_input", "warn_invalid_chars", parent=self.root)
            return

        scenario_base = os.path.join(get_base_path(), "map_images")
        new_path = os.path.join(scenario_base, name)
        if os.path.exists(new_path):
            show_ui_warning("warning_input", "warn_scenario_name_exists", parent=self.root, name=name)
            return
        try:
            os.makedirs(new_path)
        except Exception as e:
            show_ui_error("error_create_failed_title", "error_create_failed_detail", parent=self.root, error=e)
            return

        # ✅ 成功時：選択肢を更新して即反映
        scenario_list = list(self.combo_scenario["values"]) + [name]
        self.combo_scenario["values"] = scenario_list
        self.combo_scenario.set(name)
        self.on_select_scenario()
        


        # --- 説明 ---
    # Comboboxでシナリオが選択された際に呼び出される。
    # 保存先表示Entryを更新し、画像の再読み込みとキャッシュクリアを行う。
    # 最後に選択されたシナリオとして外部保存も行う。
    def on_select_scenario(self, event=None):
        name = self.combo_scenario.get()
        self.selected_scenario = name
        self.reload_map_image()
        self.update_window_title()



    def reload_map_image(self):
        # キャッシュをクリアして現在のfloor画像を再読み込み
        self.image_cache.clear()
        self.map_images = find_floor_maps(self.selected_scenario)
        self.current_floor = min(self.map_images.keys()) if self.map_images else 1
        self.refresh_floor_buttons()
        self.switch_floor(self.current_floor)
        self.canvas.config(
            width=self.map_crop.width(),
            height=self.map_crop.height()
        )

    def update_window_title(self):
        title = f"{get_ui_lang('window_title')} - {self.selected_scenario}"
        self.root.title(title)
   
    def set_window_icon(self):
        # PyInstaller対応：絶対パスに変換して確実に指定
        icon_path = os.path.abspath(os.path.join(get_base_path(), "wiz_codex.ico"))

        if os.path.exists(icon_path):
            try:
                self.root.withdraw()
                self.root.iconbitmap(icon_path)
                self.root.update_idletasks()
                self.root.deiconify()
            except Exception as e:
                print("⚠️ アイコン設定失敗:", e)
        else:
            print("⚠️ アイコンファイルが見つかりません:", icon_path)




    def open_scenario_folder(self):
        path = get_scenario_save_path(self.selected_scenario)
        try:
            os.startfile(path)
        except Exception as e:
            show_ui_error("error_title", "error_open_folder_failed", parent=self.root, error=e)


    # --- 説明 ---
    # フロア切り替えボタンの一覧を更新し、現在のマップ画像に対応したボタンを再生成する
    def refresh_floor_buttons(self):
        # 既存のボタンをすべて削除
        for widget in self.button_frame.winfo_children():
            widget.destroy()

        # フロア画像の一覧を再取得    
        self.map_images = find_floor_maps(self.selected_scenario)


        # フロアごとに切り替えボタンを生成・配置
        for f in self.map_images.keys():
            btn = tk.Button(self.button_frame, text=f"{f}F", command=lambda fl=f: self.switch_floor(fl))
            btn.pack(side=tk.LEFT)

    # --- 説明 ---
    # 指定されたフロアのマップ画像を読み込み、切り抜き処理を施して返す（キャッシュあり）
    def load_map_image(self, floor):
        print(f"[🐾] load_map_image 呼び出し: floor={floor}")

        if floor in self.image_cache:
            print(f"[📦] キャッシュヒット: {floor}")
            return self.image_cache[floor]

        filename = self.map_images.get(floor)
        print(f"[🔍] map_images[{floor}] = {filename}")
        
        if not filename:
            print(f"[⚠️] ファイル名が存在しない floor={floor}")
            return Image.new("RGB", (self.map_crop.width(), self.map_crop.height()))

        full_path = os.path.join(get_base_path(), filename)
        print(f"[📂] 読み込みパス: {full_path}")

        try:
            img = Image.open(full_path).crop(self.map_crop.as_tuple())
            self.image_cache[floor] = img
            return img
        except Exception as e:
            print(f"[💥] 画像読み込み失敗: {e}")
            return Image.new("RGB", (self.map_crop.width(), self.map_crop.height()))



    # --- 説明 ---
    # 指定されたフロアのマップ画像を読み込み、キャンバス上の画像を更新する
    def switch_floor(self, floor):
        self.current_floor = floor
        img = self.load_map_image(floor)
        if img:
            # マップ画像を更新
            self.tk_img = ImageTk.PhotoImage(img)
            self.canvas.itemconfig(self.canvas_img_id, image=self.tk_img)
            self.canvas.image = self.tk_img # ガベージコレクション対策
            self.canvas.tag_lower(self.canvas_img_id) # 座標表示用のオーバーレイ要素などを前面に表示

    # --- 説明 ---
    # DIR構造体から現在の座標・向き・フロア情報を取得し、GUIに反映する
    # 情報が取得できた場合は赤ポチ（三角）とテキストを更新し、一定間隔で再実行する
    def tick_map_overlay(self):
        """
        DIR構造体から現在の位置情報を取得し、マップUIに反映する。
        - 赤ポチ（三角）の位置と向き
        - X/Y座標と方向ラベル
        - 現在のフロア表示（floor変更時のみスイッチ実行）
        """
        try:
            if not self.dir_struct:
                self.floor_label.config(text=get_ui_lang("floor_label_loading"))
                self.canvas.after(100, self.tick_map_overlay)
                return

            # --- 情報取得 ---
            x = self.dir_struct.read_x()
            y = self.dir_struct.read_y()
            direction = self.dir_struct.read_dir()
            floor = self.dir_struct.read_floor()

            # --- 赤ポチ描画 ---
            if x is not None and y is not None and direction is not None:
                cell_width = self.profile.cell_size
                cell_height = self.profile.cell_size
                map_left = self.profile.map_origin_x - self.map_crop.left
                map_bottom = self.profile.map_origin_y - self.map_crop.top
                cx = map_left + x * cell_width
                cy = map_bottom - y * cell_height
                size = self.profile.marker_size

                if direction == 0:
                    points = [cx, cy - size, cx - size, cy + size, cx + size, cy + size]
                elif direction == 1:
                    points = [cx + size, cy, cx - size, cy - size, cx - size, cy + size]
                elif direction == 2:
                    points = [cx, cy + size, cx - size, cy - size, cx + size, cy - size]
                elif direction == 3:
                    points = [cx - size, cy, cx + size, cy - size, cx + size, cy + size]
                else:
                    points = [cx - size, cy - size, cx + size, cy - size, cx, cy + size]

                self.canvas.coords(self.marker, *points)

                dir_names = get_ui_lang("dir_names")
                dir_text = dir_names[direction] if 0 <= direction < len(dir_names) else f"? ({direction})"
                self.label_dir_xy.config(text=get_ui_lang("label_dir_xy").format(dir=dir_text, x=x, y=y))

            # --- フロア変更チェック ---
            if floor is not None and floor != getattr(self, "last_tick_floor", None):
                self.switch_floor(floor)
                if floor == 0:
                    self.floor_label.config(text=get_ui_lang("floor_label_outside"))
                else:
                    self.floor_label.config(text=get_ui_lang("floor_label_fmt").format(floor=floor))
                self.last_tick_floor = floor

        except Exception as e:
            print(f"[💥] tick_map_overlay エラー: {e}")

        self.canvas.after(100, self.tick_map_overlay)



    def capture_map_screenshot(self):
        """
        ゲームウィンドウの現在フロア画面をキャプチャし、
        現在の選択シナリオのフォルダに "map_{floor}f_full.png" として保存する。
        その後、画像リストとUIを更新。
        """
        if self.capturing:
            print("⚠️ キャプチャ中のためスキップ")
            return

        self.capturing = True
        try:
            if not self.dir_struct:
                print("📛 DIRアドレス未設定：floorが読めないため、キャプチャ中止")
                return

            hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
            if hwnd == 0:
                print("📛 ゲームウィンドウが見つかりません")
                return

            floor = self.dir_struct.read_floor()
            if floor is None:
                print("📛 floorの読み取りに失敗したため、キャプチャ中止")
                return

            # --- クライアント領域のサイズと座標取得
            width, height = win32gui.GetClientRect(hwnd)[2:4]
            left, top = win32gui.ClientToScreen(hwnd, (0, 0))
            region = (left, top, width, height)

            try:
                screenshot = pyautogui.screenshot(region=region)
            except Exception as e:
                print(f"📛 スクリーンショット取得に失敗しました: {e}")
                return

            # --- 保存先パス構築（修正済）
            folder = get_scenario_save_path(self.selected_scenario)
            if folder is None:
                print("📛 保存先の取得に失敗しました。保存中止。")
                return

            filename = f"map_{floor}f_full.png"
            save_path = os.path.join(folder, filename)

            try:
                screenshot.save(save_path, format="PNG")
                print(f"📸 {filename} を保存しました")
                print(f"📂 保存先: {folder}")
            except Exception as e:
                print(f"📛 スクリーンショット保存失敗: {e}")
                return

            # --- 🧠 キャッシュとUI更新
            self.image_cache.clear()
            self.map_images = find_floor_maps(self.selected_scenario)
            self.refresh_floor_buttons()
            self.switch_floor(floor)

        except Exception as e:
            print(f"📛 キャプチャ中に例外: {e}")

        finally:
            self.capturing = False



    # --- 説明 ---
    # DIRスキャン処理を非同期スレッドで実行する（GUIブロック回避のため）
    def rescan_and_reload(self):
        # --- ボタンの連打防止 ---
        self.btn_rescan.config(state="disabled", command=lambda: None)  # 完全封鎖

        def run_and_reenable():
            try:
                self._run_scan_thread()  # 実処理（別.py実行やサブプロセス含む）
            finally:
                def unlock():
                    # ボタンを復元（状態とコマンド両方）
                    self.btn_rescan.config(state="normal", command=self.rescan_and_reload)
                self.root.after(0, unlock)  # GUIスレッドで安全に処理

        threading.Thread(target=run_and_reenable, daemon=True).start()



    def _run_scan_thread(self):
        """
        DIR構造体の自動検出処理（内包関数 run_dir_scan を使用）。
        結果を読み込んで GUI に反映する。
        """
        try:
            # === 🆕 プロセス再取得（WIZ再起動時のゾンビハンドル対策）===
            self.handle = get_process_handle(WINDOW_TITLE)
            if not self.handle:
                show_ui_error("error_title", "error_addr_load_failed", parent=self.root)
                return

            # --- 内包版スキャン関数を実行（.csv 出力まで完了） ---
            run_dir_scan()

            # --- 出力されたアドレスを読み込み ---
            addr_dir = load_auto_dir_address()
            if addr_dir is None:
                show_ui_error("error_title", "error_addr_load_failed", parent=self.root)
                return

            # --- DIR構造体オブジェクト再構築（handle更新後の再注入）---
            self.dir_struct = DirStruct(self.handle, addr_dir)
            print(f"[✅] DIR構造体更新 → {hex(addr_dir)}")

        except Exception as e:
            import traceback
            with open("rescan_error_log.txt", "w", encoding="utf-8") as f:
                f.write("[❌ DIRスキャン失敗]\n")
                traceback.print_exc(file=f)
            show_ui_error("error_title", "error_rescan_failed", parent=self.root)

    def monitor_menu_state(self):
        last_val = None
        while True:
            try:
                # --- dir_structが未設定またはアドレス不正ならスキップ ---
                if not self.dir_struct or self.dir_struct.addr_menu_state is None:
                    time.sleep(0.6)
                    continue

                # ✅ auto_capture_enabled を1回だけ取得
                auto_enabled = self.auto_capture_enabled.get()
                if not auto_enabled:
                    time.sleep(0.6)
                    continue

                val = read_int(self.handle, self.dir_struct.addr_menu_state)

                if val is None:
                    print("📛 menu_state 読み取り失敗（val is None）")
                    time.sleep(0.6)
                    continue

                # 🔁 menu_state 変化検出条件（MAP表示に関して）
                # - 読み取り失敗→復帰 (val=None→valid) も「変化」とみなしてキャプチャ許容
                # - 将来的に menu_state を用いた他処理（UI表示等）を追加する場合は、
                #   last_val の扱いを目的別に分離する設計を検討すべし。
                if val in MAP_MENU_STATES and val != last_val and auto_enabled:
                    print("🎯 MAP表示検出！キャプチャ条件一致")
                    print(f"✅ 条件: val={val} ({hex(val)}), last_val={last_val} ({hex(last_val) if last_val is not None else 'None'}), auto={auto_enabled}")
                    self.capture_map_screenshot()
                # else:
                #     print(f"❌ 条件不一致: val={val} ({hex(val)}), last_val={last_val} ({hex(last_val) if last_val is not None else 'None'}), auto={auto_enabled}")

                last_val = val # ⛳ 処理共通の状態追跡として記録。用途が増えた場合の整理は後日でも可能

            except Exception as e:
                print(f"[monitor_menu_state エラー] {e}")

            time.sleep(0.6)

    # --- 説明 ---
    #WIZマップツールを最前面化する関数
    def toggle_topmost_window(self):
        self.root.attributes("-topmost", self.topmost_var.get())


    def on_toggle_minimap(self):
        if self.minimap_var.get():
            self.canvas.pack_forget()
            self.create_minimap_window()

            # 🔁 再スケジュール必須！！
            self.tick_mini_map()  # ←★絶対に必要！

        else:
            if self.minimap_window:
                self.minimap_window.destroy()
                self.minimap_window = None

            self.canvas.pack(before=self.label_dir_xy, expand=True, fill="both", pady=(5, 0))


    def _on_close_minimap(self):
        """×ボタンで閉じた時にも状態を同期する"""
        self.minimap_var.set(False)
        self.on_toggle_minimap()


    def create_minimap_window(self):
        cell_px = int(self.profile.cell_size)
        view_mas = 5
        view_px = cell_px * view_mas

        self.minimap_window = tk.Toplevel(self.root, bg="#1a1a1a")
        self.minimap_window.title(get_ui_lang("title_minimap"))
        self.minimap_window.geometry(f"{view_px+0}x{view_px+36}")
        self.minimap_window.resizable(False, False)
        self.minimap_window.attributes("-topmost", True)#メインメニュー状態にかかわらず常に最前面表示
        #self.minimap_window.overrideredirect(True)  # ✅ メニューバー非表示

                
        # ✅ 「×」ボタンで閉じた時の動作を明示的に指定
        self.minimap_window.protocol("WM_DELETE_WINDOW", self._on_close_minimap)

        # --- キャンバス生成（マウスドラッグで移動可能） ---
        self.canvas_mini = tk.Canvas(self.minimap_window, width=view_px, height=view_px,)
        self.canvas_mini.pack(pady=(16, 0))
        # 🧠 レイアウト完了後にサイズ確定！
        self.minimap_window.update_idletasks()
        # 🧼 内部Widgetのサイズを取得して、geometry再設定
        self.minimap_window.geometry(f"{view_px+0}x{view_px+36}")

        def start_move(event):
            self.minimap_window._drag_start_x = event.x
            self.minimap_window._drag_start_y = event.y

        def do_move(event):
            x = self.minimap_window.winfo_x() + event.x - self.minimap_window._drag_start_x
            y = self.minimap_window.winfo_y() + event.y - self.minimap_window._drag_start_y
            self.minimap_window.geometry(f"+{x}+{y}")

        # Canvasへのバインド
        self.canvas_mini.bind("<ButtonPress-1>", start_move)
        self.canvas_mini.bind("<B1-Motion>", do_move)

        # Toplevel全体にもバインド
        self.minimap_window.bind("<ButtonPress-1>", start_move)
        self.minimap_window.bind("<B1-Motion>", do_move)

        self.update_mini_map()



    def crop_mini_map_image(self, floor):
        """
        通常マップと同じ基準中心（10,10）にプレイヤーを固定し、
        プレイヤーが動いたらCrop範囲をずらす方式で、正確な追従を行う。
        """
        try:
            full_path = os.path.join(get_base_path(), self.map_images[floor])
            img_full = Image.open(full_path).convert("RGBA")
        except Exception as e:
            print(f"[ミニマップ] 元画像読み込み失敗: {e}")
            return None

        # === プレイヤー座標取得 ===
        x = self.dir_struct.read_x()
        y = self.dir_struct.read_y()
        if x is None or y is None:
            return None

        # === 中央基準をマップCropから取得 ===
        map_crop = self.profile.map_crop
        crop_cx = map_crop.left + (map_crop.width() // 2)
        crop_cy = map_crop.top + (map_crop.height() // 2)

        cell_px = int(self.profile.cell_size)
        view_mas = 5
        view_px = cell_px * view_mas

        # === プレイヤーの差分で中心を補正（10,10を基準とする） ===
        dx = x - 9
        dy = y - 9
        px = crop_cx + dx * cell_px
        py = crop_cy - dy * cell_px  # Y軸反転

        # === Crop範囲 ===
        left = px - (view_px // 2)
        top = py - (view_px // 2)
        right = left + view_px
        bottom = top + view_px

        bg = Image.new("RGBA", (view_px, view_px), (0, 0, 0, 0))

        src_box = (
            max(0, left),
            max(0, top),
            min(right, img_full.width),
            min(bottom, img_full.height)
        )

        dst_box = (
            max(0, -left),
            max(0, -top)
        )

        try:
            crop_part = img_full.crop(src_box)
            bg.paste(crop_part, dst_box, crop_part)
            return bg
        except Exception as e:
            print(f"[ミニマップ] 合成失敗: {e}")
            return None


    def update_mini_map(self):
        if not self.dir_struct or not hasattr(self, "canvas_mini"):
            print("❌ dir_struct または canvas_mini が未定義")
            return

        if not self.canvas_mini.winfo_exists():
            print("[tick_mini_map] キャンバス破棄済み → 更新停止")
            return

        x = self.dir_struct.read_x()
        y = self.dir_struct.read_y()
        dir = self.dir_struct.read_dir()
        floor = self.current_floor

        if x is None or y is None or dir is None:
            return

        img = self.crop_mini_map_image(floor)
        if img is None:
            return

        self.tk_img_mini = ImageTk.PhotoImage(img)

        # ✅ 画像の再描画（itemconfig or 新規生成）
        if hasattr(self, "canvas_img_mini_id") and self.canvas_mini.find_withtag(self.canvas_img_mini_id):
            try:
                self.canvas_mini.itemconfig(self.canvas_img_mini_id, image=self.tk_img_mini)
            except Exception as e:
                print(f"[update_mini_map] 再描画失敗: {e} → 新規作成に切り替え")
                self.canvas_img_mini_id = self.canvas_mini.create_image(0, 0, image=self.tk_img_mini, anchor=tk.NW)
        else:
            self.canvas_img_mini_id = self.canvas_mini.create_image(0, 0, image=self.tk_img_mini, anchor=tk.NW)

        self.canvas_mini.image = self.tk_img_mini  # GC防止

        # ✅ 赤ポチ描画（中央固定）
        cx = cy = img.width // 2
        m = self.profile.marker_size
        if dir == 0:
            points = [cx, cy - m, cx - m, cy + m, cx + m, cy + m]
        elif dir == 1:
            points = [cx + m, cy, cx - m, cy - m, cx - m, cy + m]
        elif dir == 2:
            points = [cx, cy + m, cx - m, cy - m, cx + m, cy - m]
        elif dir == 3:
            points = [cx - m, cy, cx + m, cy - m, cx + m, cy + m]
        else:
            points = [cx - m, cy - m, cx + m, cy - m, cx, cy + m]

        self.canvas_mini.delete("marker")
        self.canvas_mini.create_polygon(*points, fill="red", tag="marker")


    def tick_mini_map(self):
        try:
            if hasattr(self, "canvas_mini") and self.canvas_mini.winfo_exists():
                self.update_mini_map()
        except Exception as e:
            print(f"[tick_mini_map] 例外: {e}")

        # 🔁 ループ維持（必ず再スケジュール）
        target = self.canvas_mini if hasattr(self, "canvas_mini") and self.canvas_mini.winfo_exists() else self.root
        target.after(100, self.tick_mini_map)




########敵HP調査関連#########

# メモリ走査関連定義
PAGE_READWRITE = 0x04 # ページ保護属性：PAGE_READWRITE（WinAPI定数）
MEM_REGION_ALIGN = 0x1000  # メモリページ境界（通常4KB）

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
    ]

# --- MEM_COMMIT | PAGE_READWRITE 領域の列挙 ---
def get_valid_regions(pm):
    """
    対象プロセス内の MEM_COMMIT | PAGE_READWRITE 領域を列挙
    戻り値: [(base_address, region_size), ...]
    """
    regions = []
    address = 0x0
    mem_info = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mem_info)

    while address < 0x7FFFFFFFFFFF:
        result = ctypes.windll.kernel32.VirtualQueryEx(
            pm.process_handle,
            ctypes.c_void_p(address),
            ctypes.byref(mem_info),
            mbi_size
        )
        if not result:
            address += 0x1000  # MEM_REGION_ALIGN（ページ境界）にスキップ
            continue

        base_address = mem_info.BaseAddress
        region_size = mem_info.RegionSize

        if base_address and region_size > 0:
            addr_val = int(base_address)

            if mem_info.State == 0x1000 and mem_info.Protect == 0x04:  # MEM_COMMIT, PAGE_READWRITE
                regions.append((addr_val, region_size))

            address = addr_val + region_size
        else:
            address += 0x1000

    return regions


def scan_enemy_hp_addr(memdump: dict):
    """
    サンドイッチスキャン：
    味方の現在HP + 最大HP の一致に基づいて敵HP構造体を検出する。
    memdump: {addr: bytes} 構造体ベースアドレス → 構造体bytes（最低0x198バイト必要）
    """
    ally_cur_hp_list = [23, 0, 10, 89, 0, 56]
    ally_max_hp_list = [45, 10, 100, 89, 30, 56]

    matched_addrs = []

    for base, blob in memdump.items():
        if len(blob) < 0x198:
            continue  # 十分なサイズでないblobは除外

        try:
            cur_hp = [int.from_bytes(blob[0x000 + i*4 : 0x000 + (i+1)*4], 'little') for i in range(6)]
            max_hp = [int.from_bytes(blob[0x180 + i*4 : 0x180 + (i+1)*4], 'little') for i in range(6)]

            if cur_hp == ally_cur_hp_list and max_hp == ally_max_hp_list:
                enemy_hp = int.from_bytes(blob[0x030:0x034], 'little')
                print(f"[HIT] addr={hex(base)}  enemy_hp={enemy_hp}")
                matched_addrs.append(base)

        except Exception as e:
            print(f"[WARN] blob parse error at {hex(base)}: {e}")
            continue

    print("=== サンドイッチスキャン完了 ===")
    return matched_addrs


########敵HP調査関連#########

# --- メイン実行部（GUI起動の軸） ---
if __name__ == "__main__":
    try:
        # ゲームのプロセスハンドルを取得
        handle = get_process_handle(WINDOW_TITLE)

        # GUIウィンドウ作成（初期は非表示）
        root = tk.Tk()
        # ウィンドウアイコン設定
        # ウィンドウアイコン設定（存在しない・壊れている場合も起動は続ける）
        try:
            icon_path = os.path.join(get_base_path(), "wiz_codex.ico")
            root.iconbitmap(icon_path)
        except Exception as e:
            print(f"⚠️ アイコンの読み込みに失敗しました: {e}")

        # DIRアドレスのCSV読み込み（失敗しても続行）
        try:
            addr_dir = load_auto_dir_address()
        except Exception:
            addr_dir = None

        # ウィンドウ表示＆アプリ初期化
        root.deiconify()
        root.title(get_ui_lang("window_title"))

        app = MapApp(root, handle, addr_dir)

        # 🔄 自動キャプチャ監視スレッドの起動
        
        threading.Thread(target=app.monitor_menu_state, daemon=True).start()

        root.mainloop()

    except Exception as e:
        print(f"エラー: {e}")




