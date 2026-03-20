"""
123pan-upload-python-example
- 123云盘上传实例，通过简单的API封装，实现123网盘文件查询，创建文件/文件夹，文件下载本地上传等，对接OPENAI
- https://gitee.com/lyd-code/123pan-upload-python-example
- 官方文档：https://123yunpan.yuque.com/org-wiki-123yunpan-muaork/cr6ced
"""

import os
import time
import json
import math
import random
import hashlib
import requests
import functools
from datetime import datetime
from urllib.parse import urlparse, urlencode, unquote

API = ['/api/v1/access_token',  # 获取access_token 限制QPS:1
       '/api/v1/user/info',  # 获取用户信息 限制QPS:1 API： GET 域名 + /api/v1/user/info
       # ------------------------
       '/upload/v1/file/mkdir',  # 创建目录 限制QPS:2 API： POST   域名 + /upload/v1/file/mkdir
       '/upload/v1/file/create',  # 创建文件 限制QPS:2 API： POST   域名 + /upload/v1/file/create
       '/upload/v1/file/list_upload_parts',
       # 列举已上传分片 API： POST 域名 + /upload/v1/file/list_upload_parts 说明:该接口用于最后一片分片上传完成时,列出云端分片供用户自行比对。比对正确后调用上传完毕接口。当文件大小小于 sliceSize 分片大小时,无需调用该接口。该结果将返回空值。
       '/upload/v1/file/get_upload_url',  # 获取上传地址 API： POST 域名 + /upload/v1/file/get_upload_url
       '/upload/v1/file/upload_complete',  # 上传完毕 API： POST   域名 + /upload/v1/file/upload_complete  说明：文件上传完成后请求
       '/upload/v1/file/upload_async_result',
       # 异步轮询获取上传结果 API： POST   域名 + /upload/v1/file/upload_async_result 说明：异步轮询获取上传结果
       # ------------------------
       '/api/v1/file/move',  # 移动文件 限制QPS:1 API： POST 域名 + /api/v1/file/move 说明：批量移动文件，单级最多支持100个
       '/api/v1/file/trash',  # 删除文件至回收站 限制QPS:1 API： POST 域名 + /api/v1/file/trash 说明：删除的文件，会放入回收站中
       '/api/v1/file/recover',  # 从回收站恢复文件 API： POST 域名 + /api/v1/file/recover 说明：将回收站的文件恢复至删除前的位置
       '/api/v1/file/delete',  # 彻底删除文件API： POST 域名 + /api/v1/file/delete 说明：彻底删除文件前,文件必须要在回收站中,否则无法删除
       '/api/v1/file/list',  # 获取文件列表 限制QPS:4 API： GET 域名 + /api/v1/file/list
       # ------------------------
       '/api/v1/share/create',  # 创建分享链接 API： POST 域名 + /api/v1/share/create
       # ------------------------
       '/api/v1/offline/download',  # 创建离线下载 任务离线下载任务仅支持 http/https任务创建  API： POST 域名 + /api/v1/offline/download
       # ------------------------
       '/api/v1/direct-link/enable',  # 启用直链空间 API： POST 域名 + /api/v1/direct-link/enable
       '/api/v1/direct-link/disable',  # 禁用直链空间 API： POST 域名 + /api/v1/direct-link/disable
       '/api/v1/direct-link/url',  # 获取直链链接 API： GET 域名 + /api/v1/direct-link/url
       '/api/v1/direct-link/queryTranscode',  # 查询直链转码进度 API： POST 域名 + /api/v1/direct-link/queryTranscode
       '/api/v1/direct-link/doTranscode',  # 发起直链转码 API： POST 域名 + /api/v1/direct-link/doTranscode
       '/api/v1/direct-link/get/m3u8',  # 获取直链转码链接 API： GET 域名 + /api/v1/direct-link/get/m3u8
       # ------------------------------新增
       '/api/v1/file/rename',  # 重命名 API： POST 域名 + /api/v1/file/rename 说明：批量重命名文件，最多支持同时30个文件重命名
       '/api/v1/file/detail',  # 获取文件详情 API： GET 域名 + /api/v1/file/detail
       '/api/v2/file/list',  # 获取文件列表（推荐） API： GET 域名 + /api/v2/file/list

       ]


class openapi_123pan:
    # 初始化
    def __init__(self):
        self.read_ini()
        self.header = {
            'Platform': 'open_platform',
            "Authorization": self.authorization,
            "Content-Type": "application/json"
        }
        self.base_url = 'https://open-api.123pan.com'

    # 读取api相关信息
    def read_ini(self):
        try:
            with open('api_123pan.txt', 'r') as file:
                lines = file.readlines()
            config = {}
            for line in lines:
                key, value = line.split(':', 1)
                config[key.strip()] = value.strip()

            current_time = datetime.now()
            expiration_time = datetime.strptime(config['expiredAt'], '%Y-%m-%d %H:%M:%S')

            if current_time > expiration_time:
                self.access_token()
                with open('api_123pan.txt', 'r') as file:
                    lines = file.readlines()
                for line in lines:
                    key, value = line.split(':', 1)
                    config[key.strip()] = value.strip()

            self.authorization = config.get('access_token')
            self.expiredAt = config.get('expiredAt')
            self.private_key = config.get('private_key')
            self.uid = int(config.get('uid'))
            self.client_123id = config.get('client_123id')
            self.client_123secret = config.get('client_123secret')

        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Read failed: {e}")

    # 更新token
    def access_token(self):
        access_url = self.base_url + API[0]
        headers = {
            'Platform': 'open_platform'
        }
        data = {
            'clientID': self.client_123id,
            'clientSecret': self.client_123secret
        }
        res = requests.post(access_url, headers=headers, data=data)
        res_data = res.json().get('data')
        accessToken = res_data['accessToken']
        expiredAt = res_data['expiredAt'].replace('T', ' ')[0:19]

        # 打开文件进行读取
        with open('api_123pan.txt', 'r') as file:
            lines = file.readlines()
        # 查找并替换 access_token 和 expiredAt 的值
        for i, line in enumerate(lines):
            if line.startswith('access_token:'):
                lines[i] = f'access_token:{accessToken}\n'
            elif line.startswith('expiredAt:'):
                lines[i] = f'expiredAt:{expiredAt}\n'
        # 将修改后的内容写回文件
        with open('api_123pan.txt', 'w') as file:
            file.writelines(lines)
            # 更新类属性中的 access_token 和 expiredAt
        self.authorization = accessToken
        self.expiredAt = expiredAt

    # 获取用户信息
    def user_info(self):
        info_url = self.base_url + API[1]
        res = requests.get(info_url, headers=self.header)
        res_data = res.json()
        code = res_data.get('code')
        if code == 0:
            data = res_data.get('data')
            uid, nickname, headImage, passport, mail, spaceUsed, spacePermanent, spaceTemp, spaceTempExpr, data_traceID = \
                data.get('uid'), \
                    data.get('nickname'), data.get('headImage'), data.get('passport'), data.get('mail'), data.get(
                    'spaceUsed'), data.get('spacePermanent'), data.get('spaceTemp'), data.get(
                    'spaceTempExpr'), data.get(
                    'traceID')
            spaceUsed_GB = spaceUsed / 1024 / 1024 / 1024
            spacePermanent_TB = spacePermanent / 1024 / 1024 / 1024 / 1024
            spaceUsed = round(spaceUsed_GB, 2)
            spacePermanent = round(spacePermanent_TB, 2)
            self.uid = uid  # 用户账号id
            self.nickname = nickname  # 昵称
            self.headImage = headImage  # 头像
            self.passport = passport  # 手机号码
            self.spaceUsed = spaceUsed  # 已用空间
            self.spacePermanent = spacePermanent  # 永久空间
            self.spaceTemp = spaceTemp  # 临时空间
            self.spaceTempExpr = spaceTempExpr  # 临时空间到期日
            self.data_traceID = data_traceID  #
            self.mail = mail  # 邮箱
        else:
            print(f"错误: {code} - {res_data.get('message')}")

    # 创建目录
    def mkdir(self, date_time_name, parentID):
        mkdir_url = self.base_url + API[2]
        data = {
            'name': date_time_name,  # 目录名(注:不能重名)
            'parentID': parentID,  # 父目录id（wxxj），上传到根目录时填写 0
        }
        res = requests.post(mkdir_url, headers=self.header, data=data)
        res_data = res.json()
        code = res_data.get('code')
        if code == 0:
            dirID = res_data.get('data').get('dirID')
            return dirID
        elif code == 1:
            message = res_data.get('message')
            return message
        else:
            return False

    # 装饰器，返回false时，重复运行
    @staticmethod
    def retry_on_false(max_retries=2):
        # 定义装饰器内部的实际装饰器函数
        def decorator(func):
            # 使用 functools.wraps 保留原始函数的元数据
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                retries = 0  # 初始化重试次数计数器
                while retries < max_retries:
                    result = func(*args, **kwargs)  # 执行被装饰的函数
                    if result:  # 如果函数返回值不是 False，直接返回结果
                        return result
                    retries += 1  # 增加重试次数计数器
                    print(f'进行第{retries + 1}次上传...')
                return False  # 返回最后一次执行的结果（可能是 False）

            return wrapper  # 返回包装后的函数

        return decorator  # 返回装饰器函数本身

    # 创建文件
    @retry_on_false(max_retries=2)
    def create(self, file_path, parentFileID):
        create_url = self.base_url + API[3]
        etag = self.calculate_md5(file_path)
        size_in_bytes, file_name = self.get_file_info(file_path)
        data = {
            'parentFileID': parentFileID,  # 父目录id，上传到根目录时填写 0
            'filename': file_name,  # 文件名要小于128个字符且不能包含以下任何字符："\/:*?|><。（注：不能重名）
            'etag': etag,  # 文件md5
            'size': size_in_bytes,  # 文件大小，单位为 byte 字节
        }
        res = requests.post(create_url, headers=self.header, data=data)
        res_data = res.json()
        code = res_data.get('code')
        message = res_data.get('message')
        if code == 0:
            data = res_data.get('data')
            fileID, reuse, preuploadID, sliceSize = data.get('fileID'), data.get('reuse'), data.get(
                'preuploadID'), data.get('sliceSize')
            if not reuse:  # 当文件大小大于 sliceSize 分片大小时,无需调用该接口
                upload_data_parts = self.upload_slices(file_path, sliceSize, size_in_bytes, preuploadID)  # 列举已上传分片
                if size_in_bytes > sliceSize:  # 文件大小大于分片
                    parts_data = self.list_upload_parts(preuploadID)
                    if parts_data != upload_data_parts:  # 云端分片与本地信息比对
                        print('云端分片与本地信息比对不相同')
                        return False
                try:  # 上传完毕
                    fileID, complete_data = self.upload_complete(preuploadID)
                    res_async = complete_data.get('async')
                    completed = complete_data.get('completed')
                    if completed:
                        if fileID:  # 直接获取文件id
                            print("文件上传完成")
                            return fileID
                        print('上传完成，但fileID获取失败')
                        return False
                    elif res_async:  # 异步轮询获取上传结果
                        time.sleep(3)
                        file_ID = self.async_result(preuploadID)
                        if file_ID:
                            print('异步查询成功')
                            return file_ID
                        else:
                            searchData = ""
                            file_ID = self.list_123(parentFileID, file_name, searchData)
                            if file_ID:
                                print('异步查询有误，列表查询成功')
                                return file_ID
                            else:
                                print('列表查询失败，未找到该文件')
                                return False
                    else:
                        print('未成功上传')
                        return False
                except Exception as e:
                    print('上传结果获取', e)
                    return False
            else:
                print('文件已秒传成功')
                return fileID
        elif code == 1:
            message = res_data.get('message')
            print(message)
            if message == '该目录下文件名重复无法创建':
                searchData = ""
                file_ID = self.list_123(parentFileID, file_name, searchData)
                if file_ID:
                    print('从列表中更新file_ID')
                    return file_ID
                print('未查询到file_ID')
            return False
        elif code == 401 and message == 'token is expired':  # token过期更新
            self.access_token()
            self.__init__()
            return False
        else:
            print('其他上传错误')
            return False

    # 获取上传地址
    def get_upload_url(self, data_slices):
        try:
            upload_url = self.base_url + API[5]
            res_slices = requests.post(upload_url, headers=self.header,
                                       data=data_slices)  # 获取分片上传地址
            res_slices_data = res_slices.json().get('data')
            presignedURL = res_slices_data.get('presignedURL')
            return presignedURL
        except Exception as e:
            print('分片上传地址问题', e)
            return False

    # 上传分片
    def upload_slices(self, file_path, sliceSize, size_in_bytes, preuploadID):
        upload_data_parts = []  # 用于存储每次分片上传的数据
        num_slices = math.ceil(size_in_bytes / sliceSize)
        with open(file_path, 'rb') as file:
            for i in range(1, num_slices + 1):
                data_slices = {
                    'preuploadID': preuploadID,
                    'sliceNo': i,
                }
                presignedURL = self.get_upload_url(data_slices)
                chunk = file.read(sliceSize)  # 读取一个分片
                md5 = hashlib.md5(chunk).hexdigest()  # 计算当前块的MD5值
                response = requests.put(presignedURL, data=chunk)  # 上传分片
                if response.status_code == 200:
                    upload_data_parts.append({  # 保存每次分片上传的相关数据到列表
                        'partNumber': f'{i}',
                        'size': len(chunk),
                        'etag': md5,
                    })
            if not upload_data_parts:
                return False
            return upload_data_parts

    # 列举已上传分片
    def list_upload_parts(self, preuploadID):
        upload_parts_url = self.base_url + API[4]
        data_parts = {
            'preuploadID': preuploadID,
        }
        res_parts = requests.post(upload_parts_url, headers=self.header, data=data_parts)
        parts_data = res_parts.json().get('data').get('parts')  # 云端分片信息
        return parts_data

    # 上传完成
    def upload_complete(self, preuploadID):
        upload_complete_url = self.base_url + API[6]
        data_complete = {
            'preuploadID': preuploadID,
        }
        res_complete = requests.post(upload_complete_url, headers=self.header,
                                     data=data_complete, timeout=(3, 5))
        res_complete_data = res_complete.json()
        code = res_complete.json().get('code')
        if code == 0:
            complete_data = res_complete_data.get('data')
            fileID = complete_data.get('fileID')
            return fileID, complete_data
        return False, False
        # 异步轮询获取上传结果

    # 异步查询
    def async_result(self, preuploadID):
        async_result_url = self.base_url + API[7]
        data_async = {
            'preuploadID': preuploadID,
        }
        res_async = requests.post(async_result_url, headers=self.header,
                                  data=data_async)
        res_async_data = res_async.json()
        async_data = res_async_data.get('data')
        async_code = res_async_data.get('code')
        completed_async_data = async_data.get('completed')
        file_ID = async_data.get('fileID')
        if completed_async_data:
            return file_ID
        else:
            print('异步轮询结果错误')
            return False

    # 文件信息
    def file_info(self, fileID):
        fileinfo_url = self.base_url + API[22]
        params = {
            'fileID': fileID,  # 文件id数组,一次性最大不能超过 100 个文件
        }
        res_fileinfo = requests.get(fileinfo_url, headers=self.header, params=params)
        res_fileinfo_json = res_fileinfo.json()
        message_fileinfo = res_fileinfo_json.get('message')
        if message_fileinfo == 'ok':
            data = res_fileinfo_json.get('data')
            return data
        else:
            print(f'{message_fileinfo}')
            return False

    # 删除文件至回收站
    def trash(self, fileID):
        trash_url = self.base_url + API[9]
        fileIDs = (fileID,)
        data = {
            'fileIDs': fileIDs,  # 文件id数组,一次性最大不能超过 100 个文件
        }
        res_trash = requests.post(trash_url, headers=self.header, data=data)
        res_trash_json = res_trash.json()
        message_trash = res_trash_json.get('message')
        if message_trash == 'ok':
            print('成功删除')
            return True
        else:
            print(f'{message_trash}')
            return False

    def rename(self, renameList):
        rename_url = self.base_url + API[21]
        data = {
            # 数组,每个成员的格式为 文件ID|新的文件名,批量重命名文件，最多支持同时30个文件重命名,
            'renameList': renameList,
        }
        res_rename = requests.post(rename_url, headers=self.header, data=data)
        res_rename_json = res_rename.json()
        message_rename = res_rename_json.get('message')
        if message_rename == 'ok':
            print('重命名成功')
            return True
        else:
            print(f'{message_rename}')
            return False

    # 移动文件
    def move(self, file_ID, toParentFileID):
        move_url = self.base_url + API[8]
        fileIDs = (file_ID,)
        data = {
            'fileIDs': fileIDs,  # 文件id数组,一次性最大不能超过 100 个文件
            'toParentFileID': toParentFileID,  # 要移动到的目标文件夹id，移动到根目录时填写 0
        }
        res_move = requests.post(move_url, headers=self.header, data=data)
        res_move_json = res_move.json()
        message_trash = res_move_json.get('message')
        if message_trash == 'ok':
            print('文件已移动')
            return True
        else:
            print(f'{message_trash}')
            return False

    # 获取文件列表
    def list_123(self, parentFileId, file_name, searchData):
        list_oldurl = self.base_url + API[12]
        list_newurl = self.base_url + API[23]
        if not searchData:
            # 旧版查询
            for page in range(1, 5):
                limit = 100
                orderBy = 'createAt'  # 创建时间 必填	排序字段,例如:file_id、size、file_name,createAt
                params = {
                    'parentFileId': parentFileId,  # 必填	文件夹ID，根目录传 0
                    'page': page,  # 必填	页码数
                    'limit': limit,  # 必填	每页文件数量，最大不超过100
                    'orderBy': orderBy,  # 必填	排序字段,例如:file_id、size、file_name
                    'orderDirection': 'desc',  # 必填	排序方向:asc、desc
                    'trashed': False,  # 选填	是否查看回收站的文件
                    'searchData': '',  # 选填	搜索关键字
                }
                res_oldlist = requests.get(list_oldurl, headers=self.header, params=params)
                res_oldlist_json = res_oldlist.json()
                oldlist = res_oldlist_json.get('data').get('fileList')
                for olditem in oldlist:
                    if olditem['filename'] == file_name:
                        fileID = olditem['fileID']
                        if fileID:
                            return fileID
                        else:
                            continue
        else:
            # 新版查询
            for page in range(1, 5):
                limit = 100
                params = {
                    'parentFileId': parentFileId,  # 必填	文件夹ID，根目录传 0
                    'lastFileId': page,  # 必填	页码数
                    'limit': limit,  # 必填	每页文件数量，最大不超过100
                    'searchData': searchData,  # 选填	搜索关键字
                    'searchMode': 1,  # 选填	搜索关键字
                }
                res_newlist = requests.get(list_newurl, headers=self.header, params=params)
                res_newlist_json = res_newlist.json()
                newlist = res_newlist_json.get('data').get('fileList')
                for newitem in newlist:
                    if newitem['filename'] == file_name:
                        file_ID = newitem['fileId']
                        if file_ID:
                            return file_ID
                        else:
                            continue

    # 创建分享链接
    def share_create(self, file_ID, shareName, shareExpire, sharePwd):
        share_url = self.base_url + API[13]
        fileIDList = [file_ID, ]
        base_url = 'https://www.123pan.com/s/'
        data = {
            'shareName': shareName,  # 必填	分享链接
            'shareExpire': shareExpire,  # 必填	分享链接有效期天数,该值为枚举 固定只能填写:1、7、30、0 填写0时代表永久分享
            'fileIDList': fileIDList,  # 必填	分享文件ID列表,以逗号分割,最大只支持拼接100个文件ID,示例:1,2,3
            'sharePwd': sharePwd,  # 选填	设置分享链接提取码
        }
        res_share = requests.post(share_url, headers=self.header, data=data)
        res_share_json = res_share.json()
        share_data = res_share_json.get('data')
        message = res_share_json.get('message')
        if message == 'ok':
            shareKey = share_data.get('shareKey')
            share_url = base_url + shareKey
            print(share_url)
            return share_url
        else:
            print('未成功获取分享链接')
            return False

    # 获取直链链接
    def direct_link_url(self, fileID):
        direct_link_url = self.base_url + API[17]
        params = {
            'fileID': fileID,  # 文件id
        }
        try:
            res_direct = requests.get(
                direct_link_url, headers=self.header, params=params)
        except requests.exceptions.RequestException as e:
            # 记录错误
            print(f"连接错误: {e}")
            return False
        if res_direct.status_code == 200:
            res_direct_json = res_direct.json()
            code = res_direct_json.get('code')
            message = res_direct_json.get('message')
            if code == 0:
                direct_url = res_direct_json.get('data').get('url')
                return direct_url
            else:
                print(message)
                return False
        else:
            print(res_direct.status_code, '请求错误')
            return False

    # 获取鉴权直链链接
    def direct_link_auth_key_url(self, direct_url):
        # 链接签名有效期，单位：秒
        valid_duration = (60 * 60 * 24 * 1)  # 1.01天
        # 使用sign_url函数生成带有签名参数的URL
        new_url, params = self.sign_url(direct_url, self.private_key, valid_duration)
        return new_url

    # 生成直链鉴权
    def sign_url(self, origin_url, private_key, valid_duration):
        # 生成有效时间戳，单位：毫秒
        ts = int(time.time() + valid_duration)
        # 生成随机正整数，最大值为1000000
        r_int = random.randint(0, 1000000)
        # 解析原始URL
        parsed_url = urlparse(origin_url)
        # 对路径进行URL解码，替代JavaScript中的decodeURIComponent
        path = unquote(parsed_url.path)
        # 构建需要签名的数据
        data_to_sign = f"{path}-{str(ts)}-{str(r_int)}-{private_key}"
        # 使用MD5哈希函数计算签名
        hashed_data = hashlib.md5(data_to_sign.encode()).hexdigest()
        # 构建查询参数
        query_params = {'auth_key': f"{ts}-{r_int}-{hashed_data}"}
        # 构建带有签名参数的URL
        signed_url = f"{parsed_url.scheme}://{parsed_url.netloc}{path}?{urlencode(query_params)}"
        return signed_url, urlencode(query_params)

    # 文件id到鉴权直链
    def fileid_to_authurl(self, file_ID):
        # 长连接
        direct_url = self.direct_link_url(file_ID)
        new_url = self.direct_link_auth_key_url(direct_url)
        return new_url

    # 计算文件的 MD5
    @staticmethod
    def calculate_md5(file_path):
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as file:
            content = file.read()
            md5_hash.update(content)
        etag = md5_hash.hexdigest()
        return etag

    # 读取文件信息
    @staticmethod
    def get_file_info(file_path):
        # 使用 os.path.basename 获取文件名（包括路径）
        file_name = os.path.basename(file_path)
        # 使用 os.path.getsize 获取文件大小（以字节为单位）
        size_in_bytes = os.path.getsize(file_path)
        return size_in_bytes, file_name

