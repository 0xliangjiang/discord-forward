

import selfcord
import asyncio
import csv
import os

TOKEN = "MTIzNTgwNTY3MTc5OTI2MzI3NA.GwD2m8.Y5jE_bCgKsIwlOl8myFVRAKZw-6NOCsulsPZWo"
GUILD_ID = 1340918593067679799
OUTPUT_FILE = "guild_members.csv"

class MySelfcordClient(selfcord.Client):
    async def on_ready(self):
        print(f'已登录账号: {self.user}')
        guild = self.get_guild(GUILD_ID)
        if not guild:
            print("找不到服务器")
            await self.close()
            return

        print(f"服务器名称: {guild.name}")
        print("正在导出已缓存成员...")

        with open(OUTPUT_FILE, "w", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "用户名", "昵称", "Discriminator", "Bot", "加入时间"])
            for member in guild.members:
                # 只拼接非0 discriminator
                if member.discriminator != "0":
                    username = f"{member.name}#{member.discriminator}"
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
        print(f"已导出 {len(guild.members)} 个成员到 {OUTPUT_FILE}")
        os._exit(0)  # 直接退出进程，彻底避免 selfcord 关闭时报错

client = MySelfcordClient()
asyncio.run(client.start(TOKEN))