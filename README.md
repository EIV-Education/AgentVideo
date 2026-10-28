# AgentVideo

**AgentVideo là một AI agent (Claude + FFmpeg) nhận yêu cầu bằng ngôn ngữ thường (tiếng Việt hoặc tiếng Anh), rồi tự lên kế hoạch, cắt ghép, chỉnh sửa và xuất video hoàn chỉnh.** Agent có **21 công cụ** chỉnh sửa, tự xem khung hình để kiểm tra kết quả, và chỉ đọc/ghi trong một thư mục làm việc.

```bash
agentvideo "Ghép intro.mp4 và bai1.mp4 có hiệu ứng fade, chuyển sang 9:16, thêm chữ 'EIV Education' ở trên, nhạc nền music.mp3 nhỏ" -w ./project
```

## Agent làm được những gì?

| Nhóm | Công cụ | Ví dụ yêu cầu |
|---|---|---|
| Phân tích | `list_files`, `probe_media`, `view_frames`, `detect_scenes`, `detect_silence` | "Xem video này có mấy cảnh?" |
| Cắt ghép | `trim_video`, `concat_videos` (fade, dissolve, slide…), `remove_segments`, `change_speed` | "Cắt bỏ các đoạn im lặng trong bài giảng" |
| Khung hình | `reframe_video` (16:9, 9:16, 1:1, 4:5, 4:3; crop / pad / nền mờ) | "Chuyển video YouTube sang Reels" |
| Chữ & phụ đề | `add_text_overlay`, `write_subtitles`, `burn_subtitles` | "Thêm phụ đề tiếng Việt cho 3 câu thoại" |
| Âm thanh | `add_audio_track` (mix / replace), `extract_audio` | "Thêm nhạc nền âm lượng 30%, fade cuối" |
| Hiệu ứng | `apply_effects` (sáng, tương phản, bão hoà, đen trắng, fade, xoay, lật, âm lượng), `overlay_image` (logo) | "Gắn logo góc phải, fade in 1 giây" |
| Tạo video mới | `create_title_card`, `images_to_video` (Ken Burns + chuyển cảnh) | "Tạo video 30s từ 6 ảnh, có intro và outro" |
| Xuất file | `convert_or_compress` (.mp4, .webm, .mov, .gif) | "Nén còn dưới 20 MB" |
| Dự phòng | `run_ffmpeg` (lệnh ffmpeg thô, bị giới hạn trong workspace) | Các thao tác đặc biệt |

**Giới hạn:** agent không tự sinh cảnh quay chân thực (text-to-video kiểu Veo/Runway) và không tự đọc giọng (TTS). Agent dựng video từ footage, ảnh, chữ, màu nền và file âm thanh có sẵn.

## Cài đặt thế nào?

1. Cài **FFmpeg** (có `ffmpeg` và `ffprobe` trong PATH) và **Python ≥ 3.10**.
2. Cài gói:
   ```bash
   pip install -e .
   ```
3. Đặt API key của Claude:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

## Dùng thế nào?

1. Bỏ video, ảnh, nhạc vào một thư mục, ví dụ `./project`.
2. Chạy một lệnh:
   ```bash
   agentvideo "Cắt từ giây 5 đến 35 của lecture.mp4, bỏ đoạn im lặng, thêm tiêu đề 'Bài 3: Present Perfect'" -w ./project
   ```
3. Hoặc chế độ hội thoại (sửa tiếp nhiều lượt, agent nhớ ngữ cảnh):
   ```bash
   agentvideo -w ./project
   ```

File cuối nằm trong `project/output/`, file trung gian trong `project/work/`. File gốc không bị ghi đè.

Tuỳ chọn: `-m` đổi model (mặc định `claude-opus-5-5`), `-e low|medium|high|xhigh|max` đổi mức suy luận, `--max-turns` giới hạn số bước.

Dùng trong Python:

```python
from agentvideo import run_agent

text, history = run_agent("Tạo video dọc 9:16 từ các ảnh trong slides/", "./project")
text, history = run_agent("Thêm nhạc nền music.mp3", "./project", history=history)
```

Dùng trong **Claude Code**: repo có sẵn skill `.claude/skills/video-editor`, mở repo bằng Claude Code rồi gõ yêu cầu chỉnh video.

## Kiến trúc ra sao?

```
agentvideo/
  ffmpeg_ops.py  # thao tác FFmpeg thuần, không cần LLM, test được độc lập
  tools.py       # 21 tool cho Claude + sandbox thư mục làm việc
  agent.py       # vòng lặp tool-use: lập kế hoạch → gọi tool → kiểm tra → báo cáo
  cli.py         # lệnh `agentvideo`
tests/           # test cho mọi thao tác chính + vòng lặp agent (client giả)
```

Chạy test: `pip install -e .[dev] && pytest -q`

## So với các agent video có sẵn thì sao?

| Dự án | Hướng tiếp cận | Khác biệt so với AgentVideo |
|---|---|---|
| [video-use (browser-use)](https://github.com/browser-use/video-use) | Biến coding agent có shell thành trình dựng video, dùng transcript + thumbnail | Cần một coding agent bên ngoài; AgentVideo là CLI độc lập |
| OpenMontage | Hệ thống sản xuất lớn, nhiều pipeline, lấy footage stock | Nặng hơn; AgentVideo gọn, tập trung chỉnh sửa |
| FFMPerative, llmpeg | Dịch câu lệnh tự nhiên thành 1 lệnh ffmpeg | Không có vòng lặp nhiều bước, không tự kiểm tra kết quả |
| mcp-video, FFmpeg-MCP, Remotion MCP | MCP server cung cấp tool cho agent | Chỉ là tool, chưa có agent lập kế hoạch |
| VideoAgent (HKUDS) | Framework nghiên cứu hiểu/chỉnh/tái tạo video | Hướng nghiên cứu, cài đặt phức tạp hơn |
