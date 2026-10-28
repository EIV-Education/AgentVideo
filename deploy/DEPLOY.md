# Cài AgentVideo lên VPS đang chạy website

**Bộ cài này chạy AgentVideo trong một Docker container riêng, giới hạn CPU/RAM và ưu tiên thấp hơn website, chỉ mở cổng nội bộ `127.0.0.1:8510`, rồi đưa ra ngoài qua một subdomain riêng (ví dụ `video.tenmien.com`).** Website và agent hiện có của anh không bị đụng tới.

## Cần chuẩn bị gì?

1. **API key** của Anthropic (console.anthropic.com → API Keys), đã nạp credit và đặt giới hạn chi tiêu.
2. **Một subdomain mới**, ví dụ `video.tenmien.com`: vào trang quản lý DNS, tạo bản ghi **A** trỏ về IP của VPS.
3. Quyền **sudo** trên VPS.

## Bước 0: Kiểm tra VPS (chỉ đọc, không thay đổi gì)

```bash
git clone -b claude/inspiring-cerf-t7zhna https://github.com/EIV-Education/AgentVideo.git ~/agentvideo
```
```bash
sudo bash ~/agentvideo/deploy/check-vps.sh
```

Gửi lại toàn bộ kết quả để kiểm tra trước khi cài. Cần chú ý:

| Mục | Yêu cầu tối thiểu |
|---|---|
| RAM trống | ≥ 2 GB (agent dùng tối đa 2 GB, chỉnh bằng `MEM_LIMIT`) |
| Ổ đĩa trống | ≥ 10 GB (video tải lên và video xuất ra) |
| Docker | Đã cài, có `docker compose` |
| Web server | nginx chạy trực tiếp trên VPS thì bộ cài tự cấu hình; Apache/Caddy/Traefik/nginx trong Docker thì cần cấu hình riêng |
| Cổng 8510 | `free`; nếu bị dùng thì đổi `HOST_PORT` |

## Bước 1: Cài Docker (bỏ qua nếu đã có)

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Lệnh này không ảnh hưởng tới website đang chạy.

## Bước 2: Cài AgentVideo

```bash
sudo bash ~/agentvideo/deploy/install.sh video.tenmien.com
```

Script sẽ hỏi API key và mật khẩu chung cho team, sau đó tự làm:

1. Build và chạy container (lần đầu mất vài phút, build chạy với ưu tiên thấp `nice`).
2. Kiểm tra app đã chạy qua `/healthz`.
3. Thêm **một file nginx riêng** `agentvideo.conf` chỉ cho `video.tenmien.com`, kiểm tra bằng `nginx -t`, rồi **reload** (không restart, website không bị gián đoạn). Nếu kiểm tra lỗi, script tự xoá file vừa thêm.
4. Xin chứng chỉ HTTPS miễn phí bằng certbot (nếu VPS có certbot).

Chỉ muốn chạy app, chưa gắn tên miền: `sudo bash ~/agentvideo/deploy/install.sh`.

## Giới hạn tài nguyên mặc định là bao nhiêu?

| Thiết lập (`deploy/.env`) | Mặc định | Tác dụng |
|---|---|---|
| `CPU_LIMIT` | `1.5` | Agent dùng tối đa 1,5 nhân CPU |
| `MEM_LIMIT` | `2g` | Agent dùng tối đa 2 GB RAM |
| `cpu_shares` (trong compose) | `256` | Khi CPU bận, website (mức 1024) được ưu tiên gấp 4 lần |
| `MAX_CONCURRENT_JOBS` | `1` | Mỗi lúc xử lý 1 video, người khác xếp hàng |
| `MAX_UPLOAD_MB` | `1024` | Tải lên tối đa 1 GB mỗi lần |
| `JOB_TTL_HOURS` | `48` | Tự xoá file sau 48 giờ để không đầy ổ |
| `AGENTVIDEO_MODEL` | `claude-sonnet-5-5` | Model rẻ hơn cho giai đoạn đầu |

Sửa `deploy/.env` xong thì chạy lại `sudo bash ~/agentvideo/deploy/install.sh video.tenmien.com`.

## Vận hành hằng ngày

Xem log:
```bash
sudo docker logs -f --tail 100 agentvideo
```

Xem agent đang dùng bao nhiêu CPU/RAM:
```bash
sudo docker stats agentvideo --no-stream
```

Cập nhật bản mới:
```bash
cd ~/agentvideo && git pull && sudo bash deploy/install.sh video.tenmien.com
```

Tạm dừng / chạy lại:
```bash
sudo docker stop agentvideo
```
```bash
sudo docker start agentvideo
```

## Gỡ bỏ hoàn toàn

```bash
cd ~/agentvideo/deploy && sudo docker compose down
```
```bash
sudo rm -f /etc/nginx/sites-enabled/agentvideo.conf /etc/nginx/sites-available/agentvideo.conf /etc/nginx/conf.d/agentvideo.conf && sudo nginx -t && sudo systemctl reload nginx
```

Website không bị ảnh hưởng khi gỡ, vì bộ cài chỉ thêm đúng file `agentvideo.conf`.

## VPS dùng aaPanel thì làm thế nào?

**Với aaPanel, script chỉ chạy app trong Docker; tên miền và HTTPS làm trong giao diện aaPanel.** Script tự nhận ra aaPanel và không sửa nginx của panel.

1. Mở **Terminal** trong menu aaPanel, chạy `check-vps.sh` và `install.sh` như các bước trên (không cần SSH).
2. **Website → Add site**: nhập `video.tenmien.com`, PHP chọn **Static**, không tạo database/FTP.
3. Mở site vừa tạo → **Reverse proxy → Add**: Target URL `http://127.0.0.1:8510`.
4. **SSL → Let's Encrypt**: chọn tên miền, bấm Apply, bật **Force HTTPS**.
5. Mở **Config** (file cấu hình) của site, thêm vào trong khối `server { ... }` rồi Save:
   ```
   client_max_body_size 1100m;
   proxy_read_timeout 600s;
   proxy_send_timeout 600s;
   proxy_request_buffering off;
   ```
   Nếu không thêm, aaPanel chặn file tải lên lớn hơn mức mặc định của panel.
