#!/bin/bash
# =====================================================================
# Vega Chat Admin CLI  —  统一管理 LibreChat 用户 / 密码 / 角色 / 注册
# =====================================================================
# LibreChat 0.8.6-rc1 没有 GUI admin 后台，所有管理走这个脚本
# 用法：vc-admin <command> [args...]
# =====================================================================

set -euo pipefail

API=vega-chat-api
MONGO=vega-chat-mongo
DB=LibreChat
COMPOSE_DIR=/opt/vega-chat/docker

usage() {
    cat <<EOF
Vega Chat Admin CLI

用法: vc-admin <命令> [参数...]

用户管理:
  list                          列出所有用户
  add <email> <name> <username> [role]
                                建用户（role 默认 USER, 可选 ADMIN）
  delete <email>                删除用户
  role <email> <ADMIN|USER>     改用户角色
  passwd <email> [新密码]       改密码（不传则随机生成并打印）
  show <email>                  查看用户详情（不含 password）

注册策略:
  open                          打开公开注册
  close                         关闭公开注册（默认）
  status                        查看当前注册策略

模型 API:
  set-key <provider> <api-key>  设 provider API key（写入 .env 然后 restart api）
                                provider: deepseek / openai / anthropic

服务:
  ps                            查容器状态
  logs [service]                看日志（默认 api，可选 mongo / meili 等）
  restart [service]             重启（默认 api）
  doctor                        全链路体检（公网 / DB / MCP）

示例:
  vc-admin add friend@example.com Friend friend
  vc-admin passwd admin@example.com MyNewStrongPass123
  vc-admin role friend@example.com ADMIN
  vc-admin open && vc-admin close   # 临时开放注册
EOF
}

mongo_exec() {
    docker exec "$MONGO" mongosh "$DB" --quiet --eval "$1"
}

bcrypt_hash() {
    docker exec "$API" node -e "console.log(require('bcryptjs').hashSync('$1', 10))"
}

cmd_list() {
    mongo_exec '
        db.users.find({}, {email:1, name:1, username:1, role:1, emailVerified:1, createdAt:1, _id:0}).toArray()
    ' | sed 's/,/,\n /g'
}

cmd_show() {
    [ -z "${1:-}" ] && { echo "用法: vc-admin show <email>"; exit 1; }
    mongo_exec "JSON.stringify(db.users.findOne({email:'$1'},{password:0,refreshToken:0}), null, 2)"
}

cmd_add() {
    [ -z "${3:-}" ] && { echo "用法: vc-admin add <email> <name> <username> [role]"; exit 1; }
    local email="$1" name="$2" username="$3" role="${4:-USER}"
    local pw
    pw=$(openssl rand -base64 18)
    local hash
    hash=$(bcrypt_hash "$pw")
    mongo_exec "
        db.users.insertOne({
            email: '$email',
            password: '$hash',
            name: '$name',
            username: '$username',
            provider: 'local',
            role: '$role',
            emailVerified: true,
            twoFactorEnabled: false,
            createdAt: new Date(),
            updatedAt: new Date()
        });
        print('---NEW---');
        print(JSON.stringify(db.users.findOne({email:'$email'},{password:0})));
    "
    echo ""
    echo "================================================"
    echo " 用户已建 ✓"
    echo " 邮箱:   $email"
    echo " 用户名: $username"
    echo " 角色:   $role"
    echo " 临时密码: $pw"
    echo " (告诉用户登录后让我帮他改: vc-admin passwd $email <新密码>)"
    echo "================================================"
}

cmd_delete() {
    [ -z "${1:-}" ] && { echo "用法: vc-admin delete <email>"; exit 1; }
    read -rp "确定删除 $1 ? (yes/no): " confirm
    [ "$confirm" = "yes" ] || { echo "取消"; exit 0; }
    mongo_exec "JSON.stringify(db.users.deleteOne({email:'$1'}))"
}

cmd_role() {
    [ -z "${2:-}" ] && { echo "用法: vc-admin role <email> <ADMIN|USER>"; exit 1; }
    local role="$2"
    [[ "$role" =~ ^(ADMIN|USER)$ ]] || { echo "role 必须是 ADMIN 或 USER"; exit 1; }
    mongo_exec "JSON.stringify(db.users.updateOne({email:'$1'},{\$set:{role:'$role',updatedAt:new Date()}}))"
}

cmd_passwd() {
    [ -z "${1:-}" ] && { echo "用法: vc-admin passwd <email> [新密码]"; exit 1; }
    local email="$1" pw="${2:-}"
    if [ -z "$pw" ]; then
        pw=$(openssl rand -base64 18)
        echo "随机生成密码: $pw"
    fi
    local hash
    hash=$(bcrypt_hash "$pw")
    mongo_exec "JSON.stringify(db.users.updateOne({email:'$email'},{\$set:{password:'$hash',updatedAt:new Date()}}))"
    echo "密码已更新 ✓ ($email)"
}

cmd_open() {
    sed -i 's/^ALLOW_REGISTRATION=false/ALLOW_REGISTRATION=true/' "$COMPOSE_DIR/.env"
    cd "$COMPOSE_DIR" && docker compose restart api
    echo "公开注册已开 ✓ 用完记得 vc-admin close"
}

cmd_close() {
    sed -i 's/^ALLOW_REGISTRATION=true/ALLOW_REGISTRATION=false/' "$COMPOSE_DIR/.env"
    cd "$COMPOSE_DIR" && docker compose restart api
    echo "公开注册已关 ✓"
}

cmd_status() {
    grep '^ALLOW_REGISTRATION=' "$COMPOSE_DIR/.env"
}

cmd_set_key() {
    [ -z "${2:-}" ] && { echo "用法: vc-admin set-key <deepseek|openai|anthropic|openrouter|<env-name>> <api-key>"; exit 1; }
    local provider="$1" key="$2"
    local env_key
    case "$provider" in
        deepseek)   env_key=DEEPSEEK_API_KEY ;;
        openai)     env_key=OPENAI_API_KEY ;;
        anthropic)  env_key=ANTHROPIC_API_KEY ;;
        openrouter) env_key=OPENROUTER_API_KEY ;;
        groq)       env_key=GROQ_API_KEY ;;
        mistral)    env_key=MISTRAL_API_KEY ;;
        gemini|google) env_key=GOOGLE_API_KEY ;;
        # 已经全大写 + _API_KEY 直接当 env_key
        *)
            if [[ "$provider" =~ ^[A-Z_]+_API_KEY$ ]]; then
                env_key="$provider"
            else
                # 任意名字 → 大写 + _API_KEY 后缀
                env_key="${provider^^}_API_KEY"
            fi
            ;;
    esac
    # 已存在该 env 行 → sed 替换; 不存在 → append
    if grep -q "^${env_key}=" "$COMPOSE_DIR/.env"; then
        sed -i "s|^${env_key}=.*|${env_key}=${key}|" "$COMPOSE_DIR/.env"
    else
        echo "${env_key}=${key}" >> "$COMPOSE_DIR/.env"
    fi
    # force-recreate 让 env 真生效
    cd "$COMPOSE_DIR" && docker compose up -d --force-recreate api 2>&1 | tail -3
    echo "$env_key 已设置并 force-recreate api ✓"
}

cmd_ps() {
    cd "$COMPOSE_DIR" && docker compose ps
}

cmd_logs() {
    cd "$COMPOSE_DIR" && docker compose logs -f --tail 50 "${1:-api}"
}

cmd_restart() {
    cd "$COMPOSE_DIR" && docker compose restart "${1:-api}"
}

cmd_doctor() {
    echo "=== 容器状态 ==="
    docker ps --filter "name=vega-chat-" --format "table {{.Names}}\t{{.Status}}"
    echo ""
    echo "=== 公网可达 ==="
    curl -s -o /dev/null -w "https://chat.example.com/  -> %{http_code}\n" https://chat.example.com/
    echo ""
    echo "=== MCP Codex ==="
    docker logs "$API" 2>&1 | grep -E "MCP.*codex|tool" | tail -3
    echo ""
    echo "=== 用户数 ==="
    mongo_exec "db.users.countDocuments()"
    echo ""
    echo "=== 注册策略 ==="
    grep '^ALLOW_REGISTRATION=' "$COMPOSE_DIR/.env"
}

case "${1:-help}" in
    list)         cmd_list ;;
    show)         cmd_show "${2:-}" ;;
    add)          cmd_add "${2:-}" "${3:-}" "${4:-}" "${5:-}" ;;
    delete|rm)    cmd_delete "${2:-}" ;;
    role)         cmd_role "${2:-}" "${3:-}" ;;
    passwd|pw)    cmd_passwd "${2:-}" "${3:-}" ;;
    open)         cmd_open ;;
    close)        cmd_close ;;
    status)       cmd_status ;;
    set-key)      cmd_set_key "${2:-}" "${3:-}" ;;
    ps)           cmd_ps ;;
    logs)         cmd_logs "${2:-api}" ;;
    restart)      cmd_restart "${2:-api}" ;;
    doctor)       cmd_doctor ;;
    help|-h|--help|*) usage ;;
esac
