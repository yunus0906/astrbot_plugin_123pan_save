# astrbot_plugin_123pan_save

基于 123 盘官方 OpenAPI 实现的 AstrBot 插件。

> 开始使用前需要开通 【[开发者权益包](https://www.123pan.com/member?productKey=vip&source_page=vip_button&tabKey=1&notoken=1)】
>
> 官方文档 https://123yunpan.yuque.com/org-wiki-123yunpan-muaork/cr6ced

插件提供统一的 [`/123pan`](main.py) 指令入口，覆盖示例代码 [`api123pan.py`](api123pan.py) 中展示的全部核心能力：

- access_token 刷新
- 用户信息查询
- 创建目录
- 文件上传（含分片上传、秒传、异步轮询）
- 文件列表查询（v1 / v2）
- 文件详情
- 文件移动
- 删除到回收站
- 从回收站恢复
- 彻底删除
- 批量重命名
- 创建分享链接
- 创建离线下载任务
- 启用 / 禁用直链空间
- 获取直链
- 获取鉴权直链
- 查询转码进度
- 发起转码
- 获取 m3u8 转码链接

## 1. 配置说明

本插件提供 AstrBot 配置文件 [`_conf_schema.json`](_conf_schema.json)，推荐在 AstrBot 的插件配置界面填写以下内容：

- `client_id`：123 盘开放平台的 `clientID`
- `client_secret`：123 盘开放平台的 `clientSecret`
- `private_key`：生成鉴权直链所需的 `private_key`
- `request_timeout`：请求超时秒数，默认 `30`
- `direct_link_sign_expire_seconds`：鉴权直链有效期，默认 `86400`

插件也会兼容并读写本地凭据文件 [`api_123pan.txt`](api_123pan.txt)，用于缓存：

- `access_token`
- `expiredAt`
- `private_key`
- `uid`
- `client_123id`
- `client_123secret`

> 建议首次部署后先执行一次 [`/123pan token`](main.py)，让插件自动刷新并写入最新 token。

## 2. 指令总览

### 基础指令

- `/123pan help`
  - 查看帮助
- `/123pan token`
  - 刷新 `access_token`
- `/123pan user`
  - 查询账户信息

### 目录与上传

- `/123pan mkdir <目录名> [父目录ID]`
  - 创建目录，默认父目录为根目录 `0`
- `/123pan upload <本地文件路径> [父目录ID]`
  - 上传本地文件到指定目录
  - 相对路径会以插件目录为基准解析

示例：

```text
/123pan mkdir 备份目录 0
/123pan upload ./test.zip 0
```

### 文件列表与详情

- `/123pan list [父目录ID] [关键字] [lastFileId] [limit]`
  - 使用 v2 接口查询文件列表
- `/123pan listv1 [父目录ID] [页码] [limit] [关键字] [trashed]`
  - 使用 v1 接口查询文件列表
- `/123pan detail <fileID>`
  - 查询文件详情

示例：

```text
/123pan list 0
/123pan list 0 视频 0 20
/123pan listv1 0 1 50
/123pan detail 123456
```

### 文件管理

- `/123pan move <fileIDs逗号分隔> <目标目录ID>`
- `/123pan trash <fileIDs逗号分隔>`
- `/123pan recover <fileIDs逗号分隔>`
- `/123pan delete <fileIDs逗号分隔>`
- `/123pan rename <fileID:新名称> [更多项...]`

示例：

```text
/123pan move 1001,1002 2000
/123pan trash 1001,1002
/123pan recover 1001
/123pan delete 1001
/123pan rename 1001:新名字.mp4 1002:文档-归档.zip
```

### 分享与离线下载

- `/123pan share <fileIDs逗号分隔> <分享名称> <1|7|30|0> [提取码]`
- `/123pan offline <URL> <保存文件名> [父目录ID]`

示例：

```text
/123pan share 1001 我的分享 7 abcd
/123pan offline https://example.com/a.zip a.zip 0
```

### 直链与转码

- `/123pan direct-enable`
- `/123pan direct-disable`
- `/123pan direct-url <fileID>`
- `/123pan direct-auth <fileID>`
- `/123pan transcode-status <fileID>`
- `/123pan transcode-start <fileID>`
- `/123pan m3u8 <fileID>`

示例：

```text
/123pan direct-enable
/123pan direct-url 123456
/123pan direct-auth 123456
/123pan transcode-start 123456
/123pan transcode-status 123456
/123pan m3u8 123456
```

## 3. 返回结果说明

- 大部分管理类接口会直接返回成功提示及 JSON 数据。
- 列表、详情、离线下载、转码查询等接口默认直接输出结构化 JSON 文本。
- [`direct-auth`](main.py) 会同时返回原始直链与带 `auth_key` 的鉴权直链。

## 4. 与示例代码的对应关系

插件实现对齐 [`api123pan.py`](api123pan.py) 中的以下方法能力：

- `access_token`
- `user_info`
- `mkdir`
- `create`
- `list_upload_parts`
- `get_upload_url`
- `upload_complete`
- `async_result`
- `file_info`
- `trash`
- `rename`
- `move`
- `list_123`
- `share_create`
- `direct_link_url`
- `direct_link_auth_key_url`
- `sign_url`
- `fileid_to_authurl`

并额外补全了接口常量中声明但示例中未封装完整的方法：

- `recover`
- `delete`
- `offline_download`
- `direct_link_enable`
- `direct_link_disable`
- `query_transcode`
- `do_transcode`
- `get_m3u8`
- `file_detail`
- `file_list_v1`
- `file_list_v2`

## 5. 注意事项

1. 上传文件时，机器人运行环境必须能访问目标本地文件。
2. 若 123 盘接口返回 `token is expired`，插件会自动尝试刷新 token。
3. 生成鉴权直链前必须配置 `private_key`。
4. 某些接口的请求字段可能会因 123 盘开放平台版本更新而变化，如遇参数不匹配，请以官方文档为准微调 [`main.py`](main.py) 中 [`Pan123OpenAPI`](main.py) 的实现。
5. 分享、转码、直链等能力是否可用，取决于你的 123 盘开放平台账号权限与文件类型。

## 6. 文件说明

- [`main.py`](main.py)：插件主逻辑、API 客户端、命令处理
- [`_conf_schema.json`](_conf_schema.json)：AstrBot 插件配置项定义
- [`api_123pan.txt`](api_123pan.txt)：本地缓存凭据文件
- [`api123pan.py`](api123pan.py)：用户提供的官方 API 示例代码参考
- [`metadata.yaml`](metadata.yaml)：插件元数据
