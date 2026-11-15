#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 SequenceIdxHashByID 动画索引修复工具的中文图形界面。"""

import io
import os
import queue
import re
import subprocess
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from M2_SequenceIdxHashByID_Remapper import remap_m2_sequence_idx_hash


M2_TYPES = [("M2 模型", "*.m2"), ("所有文件", "*.*")]


def chinese_result(success, message):
    """把原脚本的返回值转换成界面状态与中文说明。"""
    if "already been processed" in message:
        return "skipped", "检测到 SEQREMAP 标记，已跳过；如需重处理，请勾选强制重新处理。"
    if message == "No changes needed":
        return "unchanged", "当前查找表已经正确，无需修改。"
    match = re.search(r"Successfully remapped (\d+) entries", message)
    if success and match:
        return "processed", "修复完成，共更新 %s 个动画索引。" % match.group(1)
    if message.startswith("Failed to parse M2 header:"):
        detail = message.split(":", 1)[1].strip()
        return "failed", "无法解析 M2 文件头：%s" % detail
    if success:
        return "processed", message
    return "failed", message


class SequenceRemapGUI:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.last_path = None

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.folder_var = tk.StringVar()
        self.force_var = tk.BooleanVar(value=False)
        self.recursive_var = tk.BooleanVar(value=False)

        root.title("M2 动画索引 SequenceIdxHashByID 修复工具")
        root.geometry("940x720")
        root.minsize(800, 620)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text=("用于修复模型降级后动画索引与实际 Sequences 不对应的问题。"
                  "它只重建查找表，不会添加模型原本没有的动画。"),
            wraplength=880,
            foreground="#444",
        ).pack(anchor="w", pady=(0, 8))

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            options,
            text="强制重新处理（忽略文件末尾的 SEQREMAP 标记）",
            variable=self.force_var,
        ).pack(side="left")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="x")
        single = ttk.Frame(notebook, padding=12)
        batch = ttk.Frame(notebook, padding=12)
        notebook.add(single, text="单个 M2 修复")
        notebook.add(batch, text="文件夹批量修复")
        single.columnconfigure(1, weight=1)
        batch.columnconfigure(1, weight=1)

        self.add_file_row(single, 0, "输入 M2：", self.input_var, self.pick_input)
        self.add_file_row(single, 1, "输出 M2：", self.output_var, self.pick_output)
        ttk.Label(
            single,
            text="默认另存为“原名_动画索引已修复.m2”，不会修改输入模型。",
            foreground="#666",
        ).grid(row=2, column=1, sticky="w", padx=8, pady=(4, 10))
        self.single_button = ttk.Button(
            single, text="开始修复单个 M2", command=self.run_single)
        self.single_button.grid(row=3, column=1, sticky="w", padx=8)

        self.add_file_row(batch, 0, "处理文件夹：", self.folder_var, self.pick_folder)
        ttk.Checkbutton(
            batch, text="包含所有子文件夹（递归处理）", variable=self.recursive_var
        ).grid(row=1, column=1, sticky="w", padx=8, pady=5)
        ttk.Label(
            batch,
            text=("注意：批量模式会修改实际有变化的原始 M2，并生成“原文件.m2.bak”备份；"
                  "无需修改或已跳过的文件不会生成备份。"),
            wraplength=650,
            foreground="#A04000",
        ).grid(row=2, column=1, sticky="w", padx=8, pady=(4, 10))
        self.batch_button = ttk.Button(
            batch, text="开始批量修复", command=self.run_batch)
        self.batch_button.grid(row=3, column=1, sticky="w", padx=8)

        self.open_button = ttk.Button(
            outer, text="打开输出位置", command=self.open_output, state="disabled")
        self.open_button.pack(anchor="w", pady=(12, 0))

        log_box = ttk.LabelFrame(outer, text="处理日志", padding=6)
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = tk.Text(log_box, wrap="word", font=("Microsoft YaHei UI", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self.set_log(
            "使用说明：\n"
            "1．单个文件：选择输入 M2 和输出位置，原文件保持不变。\n"
            "2．批量处理：选择文件夹，实际修改的原文件会自动生成 .bak 备份。\n"
            "3．已处理的文件末尾带有 SEQREMAP 标记，默认跳过。\n\n"
            "如果之后又编辑 M2 并在文件末尾追加数据，标记可能不再位于最后 64 字节，"
            "程序将无法用它识别旧处理状态。"
        )
        self.root.after(100, self.poll_events)

    def add_file_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(parent, text="浏览…", command=command).grid(row=row, column=2, pady=5)

    def pick_input(self):
        path = filedialog.askopenfilename(title="选择要修复的 M2", filetypes=M2_TYPES)
        if path:
            self.input_var.set(path)
            source = Path(path)
            self.output_var.set(str(source.with_name(source.stem + "_动画索引已修复.m2")))

    def pick_output(self):
        path = filedialog.asksaveasfilename(
            title="保存修复后的 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.output_var.set(path)

    def pick_folder(self):
        path = filedialog.askdirectory(title="选择包含 M2 的文件夹")
        if path:
            self.folder_var.set(path)

    def set_log(self, text):
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.single_button.configure(state=state)
        self.batch_button.configure(state=state)

    @staticmethod
    def normalized(path):
        return os.path.normcase(os.path.abspath(str(path)))

    def run_single(self):
        if self.busy:
            return
        try:
            source = self.input_var.get().strip()
            output = self.output_var.get().strip()
            if not source or not Path(source).is_file():
                raise ValueError("请选择有效的输入 M2 文件。")
            if not output:
                raise ValueError("请选择输出 M2 的保存位置。")
            if Path(output).suffix.lower() != ".m2":
                raise ValueError("输出文件必须使用 .m2 扩展名。")
            if self.normalized(source) == self.normalized(output):
                raise ValueError("单文件模式为保护原模型，不允许输出覆盖输入文件。")
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n%s" % output, parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法开始修复", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在分析并修复 M2，请稍候……")
        threading.Thread(
            target=self.single_worker,
            args=(source, output, self.force_var.get()),
            daemon=True,
        ).start()

    def single_worker(self, source, output, force):
        try:
            with redirect_stdout(io.StringIO()):
                success, message = remap_m2_sequence_idx_hash(source, output, force)
            status, chinese = chinese_result(success, message)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "修复失败",
                "message": "处理过程中发生错误：\n%s" % exc, "path": None,
            })
            return

        if status == "processed":
            text = "%s\n\n输出文件：\n%s\n\n输入原模型未修改。" % (chinese, output)
            self.events.put({
                "kind": "success", "title": "修复完成",
                "message": text, "path": output,
            })
        elif status == "unchanged":
            text = chinese + "\n未修改输入文件，也未生成或更新输出文件。"
            self.events.put({
                "kind": "success", "title": "无需修改",
                "message": text, "path": None,
            })
        elif status == "skipped":
            self.events.put({
                "kind": "warning", "title": "已跳过",
                "message": chinese, "path": None,
            })
        else:
            self.events.put({
                "kind": "error", "title": "修复失败",
                "message": chinese, "path": None,
            })

    @staticmethod
    def find_m2_files(folder, recursive):
        root = Path(folder)
        if recursive:
            candidates = (
                Path(current) / filename
                for current, _, filenames in os.walk(str(root))
                for filename in filenames
                if filename.lower().endswith(".m2")
            )
        else:
            candidates = (
                path for path in root.iterdir()
                if path.is_file() and path.suffix.lower() == ".m2"
            )
        return sorted((path for path in candidates if path.is_file()),
                      key=lambda path: str(path).lower())

    def run_batch(self):
        if self.busy:
            return
        try:
            folder = self.folder_var.get().strip()
            if not folder or not Path(folder).is_dir():
                raise ValueError("请选择有效的处理文件夹。")
            recursive = self.recursive_var.get()
            files = self.find_m2_files(folder, recursive)
            if not files:
                raise ValueError("所选范围内没有找到 .m2 文件。")
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法开始批量处理", str(exc), parent=self.root)
            return

        warning = (
            "找到 %d 个 M2 文件。\n\n"
            "批量模式会直接修改实际有变化的原始 M2，并生成 .bak 备份。"
            "请确认这些文件不是唯一副本。\n\n是否继续？" % len(files)
        )
        if self.force_var.get():
            warning += "\n\n当前已启用强制重新处理。"
        if not messagebox.askyesno("确认批量修改原文件", warning, parent=self.root):
            return

        self.set_busy(True)
        self.set_log("正在批量处理 %d 个 M2 文件，请稍候……" % len(files))
        threading.Thread(
            target=self.batch_worker,
            args=(folder, self.force_var.get(), recursive),
            daemon=True,
        ).start()

    def batch_worker(self, folder, force, recursive):
        counts = {"processed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
        labels = {
            "processed": "已修复",
            "unchanged": "无需修改",
            "skipped": "已跳过",
            "failed": "失败",
        }
        lines = []
        try:
            files = self.find_m2_files(folder, recursive)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "批量处理失败",
                "message": "无法枚举 M2 文件：\n%s" % exc, "path": None,
            })
            return

        for path in files:
            relative = str(path.relative_to(folder))
            try:
                with redirect_stdout(io.StringIO()):
                    success, message = remap_m2_sequence_idx_hash(str(path), None, force)
                status, chinese = chinese_result(success, message)
                counts[status] += 1
                lines.append("%s：%s（%s）" % (relative, labels[status], chinese))
            except Exception as exc:
                counts["failed"] += 1
                lines.append("%s：失败（%s）" % (relative, exc))

        summary = (
            "批量处理结束：已修复 %d 个；无需修改 %d 个；已跳过 %d 个；失败 %d 个。"
            % (counts["processed"], counts["unchanged"], counts["skipped"], counts["failed"])
        )
        text = summary + "\n\n" + "\n".join(lines)
        if counts["processed"]:
            text += "\n\n实际修复的原文件已在同目录生成 .bak 备份。"
        self.events.put({
            "kind": "warning" if counts["failed"] else "success",
            "title": "批量处理完成",
            "message": text,
            "path": folder,
        })

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                self.set_busy(False)
                kind = event["kind"]
                title = event["title"]
                message = event["message"]
                self.set_log(message)

                path = event.get("path")
                if path and Path(path).exists():
                    self.last_path = Path(path)
                    self.open_button.configure(state="normal")

                if kind == "error":
                    messagebox.showerror(title, message, parent=self.root)
                elif kind == "warning":
                    messagebox.showwarning(title, message, parent=self.root)
                else:
                    messagebox.showinfo(title, message, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def open_output(self):
        if not self.last_path:
            return
        folder = self.last_path if self.last_path.is_dir() else self.last_path.parent
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)


def main():
    root = tk.Tk()
    SequenceRemapGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
