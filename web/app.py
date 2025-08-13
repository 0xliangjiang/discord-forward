from flask import Flask, request, jsonify, send_from_directory
import json
import os
import signal
import subprocess

app = Flask(__name__, static_folder='static')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.json')

@app.route('/api/config', methods=['GET'])
def get_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return jsonify(json.load(f))

@app.route('/api/config', methods=['POST'])
def save_config():
    data = request.json
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({'success': True})

@app.route('/api/restart', methods=['POST'])
def restart_bot():
    try:
        subprocess.run(['pm2', 'restart', 'bot'], check=True)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    log_path = os.path.join(os.path.dirname(__file__), '..', 'bot.log')
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-200:]
        return jsonify({'logs': ''.join(lines)})
    except Exception as e:
        return jsonify({'logs': f'日志读取失败: {e}'})

# 新增：成员信息获取接口
@app.route('/api/people', methods=['POST'])
def export_people():
    try:
        data = request.json
        token = data.get('token')
        guild_id = data.get('guild_id')
        
        if not token or not guild_id:
            return jsonify({'success': False, 'error': 'Token和服务器ID不能为空'})
        
        # 创建临时脚本
        script_content = f'''import selfcord
import asyncio
import csv
import os

TOKEN = "{token}"
GUILD_ID = {guild_id}
OUTPUT_FILE = "guild_members.csv"

class MySelfcordClient(selfcord.Client):
    async def on_ready(self):
        print(f'已登录账号: {{self.user}}')
        guild = self.get_guild(GUILD_ID)
        if not guild:
            print("找不到服务器")
            await self.close()
            return

        print(f"服务器名称: {{guild.name}}")
        print("正在导出已缓存成员...")

        with open(OUTPUT_FILE, "w", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "用户名", "昵称", "Discriminator", "Bot", "加入时间"])
            for member in guild.members:
                # 只拼接非0 discriminator
                if member.discriminator != "0":
                    username = f"{{member.name}}#{{member.discriminator}}"
                else:
                    username = member.name
                writer.writerow([
                    member.id,
                    username,
                    member.display_name,
                    member.discriminator,
                    member.bot,
                    getattr(member, 'joined_at', '')
                ])
        print(f"已导出 {{len(guild.members)}} 个成员到 {{OUTPUT_FILE}}")
        os._exit(0)

client = MySelfcordClient()
asyncio.run(client.start(TOKEN))
'''
        
        # 写入临时脚本
        temp_script_path = os.path.join(os.path.dirname(__file__), '..', 'temp_people.py')
        with open(temp_script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        # 执行脚本
        result = subprocess.run(['python', temp_script_path], 
                              capture_output=True, text=True, timeout=30)
        
        # 删除临时脚本
        os.remove(temp_script_path)
        
        if result.returncode == 0:
            # 读取生成的CSV文件
            csv_path = os.path.join(os.path.dirname(__file__), '..', 'guild_members.csv')
            if os.path.exists(csv_path):
                with open(csv_path, 'r', encoding='utf-8') as f:
                    csv_content = f.read()
                return jsonify({
                    'success': True, 
                    'message': result.stdout,
                    'csv_content': csv_content
                })
            else:
                return jsonify({'success': False, 'error': 'CSV文件生成失败'})
        else:
            return jsonify({'success': False, 'error': result.stderr})
            
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': '执行超时'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 