# 配置沙盘部署说明

这份说明按阿里云 Linux 3（Alinux 3）来写。浏览器访问服务器的 80 端口，Nginx 把页面文件直接发出去，并把 `/api` 转到本机 8000 上的接口。账本是一个 SQLite 文件。工作日 18:30（上海时间）会自动同步行情、成交并生成快照，所以接口进程要一直开着。

系统自带的 `python3` 是 3.6，`dnf` 依赖它。安装 3.12 之后继续用 `python3.12` 这个命令，保留原来的 `python3`。

## 1. 安装 Git

```bash
sudo yum install -y git
git --version
```

## 2. 安装 Python 3.12

先看软件源里有没有现成的包。只有「Alibaba Cloud Linux 3 AI 扩展版」才提供这个包，普通的 3.2104 镜像没有。

```bash
sudo yum install -y python3.12
python3.12 --version
```

如果提示找不到 `python3.12`，用源码安装 3.12.15。1 GB 内存的机器编译时不要开优化，避免内存不够。

```bash
sudo yum install -y gcc make openssl-devel bzip2-devel libffi-devel zlib-devel \
  readline-devel sqlite-devel xz-devel wget tar
cd /tmp
curl -fLO https://www.python.org/ftp/python/3.12.15/Python-3.12.15.tgz
tar -xzf Python-3.12.15.tgz
cd Python-3.12.15
./configure --prefix=/usr/local/python312
make -j2
sudo make altinstall
sudo ln -sf /usr/local/python312/bin/python3.12 /usr/local/bin/python3.12
python3.12 --version
```

看到 `Python 3.12.15` 即可。

## 3. 安装 Node.js 22

构建页面时需要 Node.js。按机器架构下载官方包：

```bash
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) NODE_ARCH=x64 ;;
  aarch64) NODE_ARCH=arm64 ;;
  *) echo "当前架构 $ARCH 不在这份说明里"; exit 1 ;;
esac
NODE_TAR=$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | awk -v arch="$NODE_ARCH" '$2 ~ "node-v22\\..*-linux-" arch ".tar.xz" {print $2; exit}')
curl -fL -o "/tmp/${NODE_TAR}" "https://nodejs.org/dist/latest-v22.x/${NODE_TAR}"
sudo tar -C /usr/local --strip-components=1 -xJf "/tmp/${NODE_TAR}"
node --version
npm --version
```

## 4. 拉取代码并安装依赖

服务器上用 HTTPS 克隆即可：

```bash
sudo mkdir -p /opt
sudo git clone https://github.com/LuckLark28/mock-asset.git /opt/mock-asset
sudo chown -R "$USER:$USER" /opt/mock-asset
cd /opt/mock-asset
python3.12 -m venv .venv
.venv/bin/pip install -r apps/api/requirements.txt
cd apps/web
npm install
cd /opt/mock-asset
mkdir -p data
```

第一次启动接口会建立 `data/sandbox.db`，并放入示例标的和「60/30/10 股债金」方案。练习账户要在页面里自己建立。

## 5. 让接口常驻

接口只监听本机，外网不直接连 8000。

```bash
sudo tee /etc/systemd/system/sandbox-api.service >/dev/null <<'EOF'
[Unit]
Description=配置沙盘接口
After=network.target

[Service]
WorkingDirectory=/opt/mock-asset/apps/api
ExecStart=/opt/mock-asset/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
Environment=TZ=Asia/Shanghai

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sandbox-api
curl http://127.0.0.1:8000/api/health
```

健康检查返回 `{"ok":true}` 即可。

## 6. 用 Nginx 对外提供页面

先构建页面，再让 Nginx 监听 80。页面里的接口地址都是相对路径 `/api`，所以浏览器只连 Nginx，由它转到本机 8000。同步行情可能要一两分钟，读超时放到 300 秒。

```bash
cd /opt/mock-asset/apps/web
npm run build
sudo yum install -y nginx
sudo tee /etc/nginx/conf.d/sandbox.conf >/dev/null <<'EOF'
server {
    listen 80 default_server;
    server_name _;
    root /opt/mock-asset/apps/web/dist;
    client_max_body_size 20m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF
```

`default_server` 让公网 IP 的访问进这个站点。如果 `nginx -t` 报重复的 default server，再把 `/etc/nginx/nginx.conf` 里原有的 `server { ... }` 整段注释掉。

如果 SELinux 是 Enforcing，还要允许 Nginx 连接本机接口：

```bash
if [ "$(getenforce 2>/dev/null)" = "Enforcing" ]; then
  sudo setsebool -P httpd_can_network_connect 1
fi
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

浏览器打开 `http://服务器公网IP`，地址以 `http://` 开头。直接输入 IP 时，浏览器可能改走 443，而这里没有 HTTPS，页面就会显示无法访问。

阿里云安全组放行入方向 TCP 80，来源填你自己的公网 IP。8000 不要放行。新购的实例默认通常只放行 22，不放行 80 时，本机正常、自己电脑打开公网 IP 会超时。如果本机开着 firewalld，再执行：

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --reload
```

打不开时，在服务器上执行：

```bash
curl -I http://127.0.0.1
ss -lntp | grep ':80'
sudo firewall-cmd --state
```

第一行出现 `200` 或 `304`，说明 Nginx 已经在本机提供页面，接着查安全组有没有放行 TCP 80，以及浏览器是否用了 `http://` 和公网 IP。本机这一行也失败时，看 `sudo systemctl status nginx --no-pager` 和 `sudo nginx -t`。总览、方案、交易这些地址由 Nginx 回退到 `index.html`，刷新子页面也能打开。

## 7. 数据

数据库在 `/opt/mock-asset/data/sandbox.db`。里面有持仓、委托、快照和访问密码哈希。这个文件已在 `.gitignore` 里，不要提交。

页面「设置」里可以下载备份。恢复前要输入「恢复」，覆盖前会在 `data/` 留下一份当日副本。恢复之后刷新页面。

## 8. 每天自动估值

`sandbox-api` 在周一到周五 18:30（Asia/Shanghai）执行：同步行情与分红、用收盘价成交、按设置做分红再投资、生成新快照。

服务停着的时候，这一次不会补跑。恢复后到「设置」里手动点「同步行情与分红」，再点「生成快照」。

15:00 前提交的委托，要等当天收盘价到达之后才会成交。15:00 之后的委托进入下一交易日。

## 9. 更新

```bash
cd /opt/mock-asset
git pull
.venv/bin/pip install -r apps/api/requirements.txt
cd apps/web && npm install && npm run build
sudo systemctl restart sandbox-api
sudo systemctl reload nginx
```

已有数据库会在启动时补上新列。不要删掉 `data/sandbox.db`。

## 10. 访问密码

不设置时，打开 `http://服务器公网IP` 就能用。在「设置」里设了密码之后，这次浏览器会话要先输入密码。密码存在数据库里，只保留哈希。关闭密码时留空新密码并填写当前密码。
