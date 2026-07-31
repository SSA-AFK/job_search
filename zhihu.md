# 全网搜索 API

## 接口说明
该接口用于全网内容搜索。

## 接口信息

| 说明 | 值                                                        |
| :- |:---------------------------------------------------------|
| HTTP URL | https://developer.zhihu.com/api/v1/content/global_search |
| HTTP Method | GET                                                      |

## 请求参数
### Header
- Authorization：`Bearer <your_access_secret>`
- X-Request-Timestamp：秒级 Unix 时间戳
- Content-Type：固定值 `application/json`
### Query

|名称|类型|必填|说明|
| :- | :- | :- | :- |
| Query | String | 是 | 查询关键词 |
| Count | Int32 | 否 | 请求数量，默认 10，最大 20 |
| Filter | String | 否 | 高级语法筛选表达式。作为 URL Query 参数传入时需进行 URL 编码，推荐使用 `--data-urlencode` 或 SDK 参数编码能力 |
| SearchDB | String | 否 | 索引库选择，默认 `all` |

### SearchDB 索引库选择

| 值 | 说明 |
| :- | :- |
| `all` | 全部索引库，默认值 |
| `realtime` | 仅搜索实时库 |
| `static` | 仅搜索静态库 |

### Filter 高级语法

`Filter` 用于按站点、发布时间等条件过滤搜索结果。

支持字段：

| 字段 | 含义 | 类型 | 示例 |
| :- | :- | :- | :- |
| host | 站点域名 | String | `host=="example.com"` |
| publish_time | 发布时间，秒级时间戳 | Int64 | `publish_time>=1778494631` |

支持操作符：

- `host` 支持 `==`、`!=`，字符串值必须使用双引号。`host=="zhihu.com"` 及其子域名不支持，如需搜索仅知乎站内内容，请直接使用 `zhihu_search` 接口。
- `publish_time` 支持 `==`、`!=`、`>`、`>=`、`<`、`<=`，数字值不使用引号。



支持逻辑符：

- `AND`、`OR` 必须大写。
- `AND` 优先级高于 `OR`。
- 可以使用括号 `()` 明确控制优先级。

示例：

```text
host=="example.com"
host=="example.com" AND publish_time>=1778494631
(host=="example.com" OR host=="news.example.com") AND publish_time>1778494631
```

## 响应参数

Data：

|参数名|类型|是否必返|描述|
| :- | :- | :- | :- |
| HasMore | Bool | 是 | 是否有下一页数据 |
| Items | Array[Item] | 是 | 内容数据列表 |

Item：

|参数名|类型|是否必返| 描述                             |
| :- | :- | :- |:-------------------------------|
| Title | String | 是 | 内容标题                           |
| ContentType | String | 是 | 内容类型，如回答、文章                    |
| ContentID | String | 是 | 内容 Token                       |
| ContentText | String | 是 | 内容摘要，高亮部分用 <em> 标签表示           |
| Url | String | 是 | 内容链接（带溯源 utm 参数）               |
| CommentCount | Int32 | 是 | 评论数                            |
| VoteUpCount | Int32 | 是 | 赞同数                            |
| AuthorName | String | 是 | 作者昵称，匿名时，展示为：知乎用户              |
| AuthorAvatar | String | 是 | 作者头像                           |
| AuthorBadge | String | 是 | 认证标图片 Url                      |
| AuthorBadgeText | String | 是 | 认证文案                           |
| EditTime | Int64 | 是 | 最后编辑时间戳，如 1745486539           |
| CommentInfoList | Array[CommentInfo] | 否 | 精选评论                           |
| AuthorityLevel  | String             | 是 | 权威等级（1 低权威，2 中权威，3 高权威，4 超高权威） |

CommentInfo:

|参数名|类型|是否必选|描述|
| :- | :- | :- | :- |
| Content | String | 是 | 评论内容 |

### 响应示例
``` json
{
    "Code": 0,
    "Message": "success",
    "Data": {
        "HasMore": false,
        "Items": [{
            "Title": "ChatGPT现在还值得开会员吗？",
            "ContentType": "Answer",
            "ContentID": "1903044959663284716",
            "ContentText": "首先要澄清一个常见误解：ChatGPT的免费版和付费版使用的是不同模型与功能配置，体验差距确实很大。很多人用了一下免费版就觉得"就这？"，其实是没体验过付费版完整的能力，比如文件上传、多模态理解等功能。\n虽然免费版目前也使用了GPT-4-turbo模型，但功能上仍有限，例如不能用代码解释器、不支持上传文件、无长期记忆能力等，而且还有使用频率限制。\n相比之下，花20美金开通的付费版支持更多高级功能，比如处理图片、文档、复杂代码分析、图表生成等，在实际使用中效率和精度明显提升。\n如果你每天只是问几句闲聊或搜索类问题，的确不必付费，国产的一些大模型（如DeepSeek、Kimi）也能胜任。但如果你依赖它来工作学习、频繁做复杂任务，这20美元绝对是值得投入的，光省下的时间就够本。\n最后不建议拼会员，多人共用一个账号容易导致模型输出错乱，影响效果；账号安全、IP污染等问题也无法忽视。一个账号专人使用，才是最稳定、最优的体验方式。",
            "Url": "https://www.zhihu.com/answer/1903044959663284716?utm_medium=openapi_platform&utm_source=6d23634e",
            "CommentCount": 22,
            "VoteUpCount": 18,
            "AuthorName": "时光纪",
            "AuthorAvatar": "https://picx.zhimg.com/50/v2-84ce3330420f9332a1d69d4cd1f10c2f_l.jpg?source=f1558865",
            "AuthorBadge": "",
            "AuthorBadgeText": "",
            "EditTime": 1748355858,
            "CommentInfoList": [{
                "Content": "没啥区别，免费也是4o 收费你也是用4o 那o1 o3都跟智障似的 4o也差不多，但是他比较快。 本月开始不续费了，换了gemini2.5 强太多了，除了think太啰嗦，翻译还是得用回不think的模型"
            }, {
                "Content": "免费版现在也可以用gpt4o啊，只不过有限制，用的不多也够用"
            }],
            "AuthorityLevel":"2",
        }, {
            "Title": "ChatGPT电脑桌面版安装指南+使用技巧（超详细）",
            "ContentType": "Article",
            "ContentID": "18698154193",
            "ContentText": " macOS 版本：14及以上\n 处理器： 建议使用M1芯片或更新的Mac电脑，以获得最佳性能（旧款设备可能出现卡顿）。\n 下载步骤：\n1.打开浏览器，打开 OpenAI 官方下载页面：https://openai.com/chatgpt/desktop/\n2.点击 "Download for macOS" 按钮，开始下载。\n安装步骤：\n1.下载完成后，双击 .dmg 文件，将 ChatGPT 应用拖动到 "应用程序" 文件夹。\n2.如果系统提示 "来自未知开发者"，请在 "系统偏好设置">"安全性与隐私" 中点击 "仍要打开"。\n安装完成： 完成以上步骤，macOS 用户即可正常使用桌面版 ChatGPT。\n 2. Windows 用户安装指南系统时区设置： 需将电脑系统地区和时区设置为阿美莉卡（或其他OpenAI支持服务的地区）。\n1.打开电脑的"设置">"时间和语言">"日期和时间"。\n2.在"自动设置时区"中，先关闭自动设置，然后在"时区"中选择阿美莉卡（或OpenAI支持的地区）的时区。\n下载步骤：\n1.设置好之后，打开OpenAI 官方下载页面： https://openai.com/chatgpt/desktop/\n2.点击 "Download for Windows" 按钮。\n安装步骤：\n1.浏览器会自动打开到微软应用商店页面。\n2.点击 "View in Store/在Microsoft Store中查看" 按钮，跳转到微软应用商店，按照提示完成安装。\n安装完成： 完成以上步骤，Windows 用户即可正常使用桌面版 ChatGPT。\n 三、ChatGPT桌面版使用技巧安装好 ChatGPT 桌面版之后，如何充分利用它的功能，提高效率呢？\n接下来，我分享一些实用的使用技巧：\n1. 快捷键：使用快捷键可以随时随地唤出 ChatGPT，无需切换窗口，非常便捷。\nmacOS： Option + 空格Windows： Alt + 空格 (可以自定义)2. 多模态输入：截图功能： 遇到问题，直接截图发给ChatGPT，它可以帮你分析解读，无论是编程题、Excel 表格，还是其他数据报表，通通不在话下。拍照功能： 拍照上传，可以让 ChatGPT 解答数学题、识别物体等。多文件上传： 可一次性上传多个文档，让 ChatGPT 帮你总结、归纳。3. 高级语音模式：点击输入框右侧的语音图标，即可开始与 ChatGPT 进行语音对话。免费用户也可以体验高级语音模式（有体验时长限制），ChatGPT Plus用户可以享受更长时间的语音对话。4. 多窗口支持：在桌面版中，你可以同时打开多个对话窗口，方便你同时进行多个任务。设置方式：鼠标放到左侧栏相应对话后的"···"，在选项弹窗中选择"在伴随浮窗中打开"。5. 自定义快捷键：如果你觉得默认的快捷键用着不习惯，可以在系统设置中自定义快捷键，让操作更加顺手。设置方式：点击左下角的账号头像>设置>应用，选择"伴随浮窗热键"进行更改。6. 直接启动第三方应用（macOS 独享）：macOS的ChatGPT Plus/Pro和Teams订阅用户，可以直接在ChatGPT中启动VS Code、Xcode、Terminal等第三方应用，进行跨应用协作。对于编辑器类应用，ChatGPT能够读取最前窗口的完整内容；对于终端类应用，可以读取最后200行内容。四、桌面版跟网页版有什么不一样？ChatGPT 桌面版和网页版虽然都使用相同的模型，但使用体验却大相径庭。\n来看一下两者之间的主要区别：\n如果你是一个经常要用的ChatGPT的用户，从效率和功能角度看，桌面版无疑是更好的选择。\n五、ChatGPT Plus或Pro方法不管是哪个端，如果你想解锁ChatGPT的全部功能，包括o1模型、sora、task、高级语音模式等，就需要订阅 ChatGPT Plus或者Pro。\n具体可以看⬇️：\nChatGPT Plus如何升级订阅最新方法全网汇总以上。\n如果有啥疑问也可以在留言告诉我。",
            "Url": "https://zhuanlan.zhihu.com/p/18698154193?utm_medium=openapi_platform&utm_source=6d23634e",
            "CommentCount": 15,
            "VoteUpCount": 27,
            "AuthorName": "文字机器凸哥",
            "AuthorAvatar": "https://picx.zhimg.com/50/v2-df39523084f28b407d21394b6210653c_l.jpg?source=f1558865",
            "AuthorBadge": "",
            "AuthorBadgeText": "",
            "EditTime": 1753954052,
            "CommentInfoList": [{
                "Content": "今天发现有桌面端 下下来后才发现似乎与网页端没什么区别 伴随浮窗无法使用 alt+space快捷键仅仅是呼出/隐藏桌面端主窗口 不知道为什么"
            }, {
                "Content": "显示网络设置有问题咋办[发呆]"
            }],
            "AuthorityLevel":"1",
        }]
    }
}
```


## 代码示例
Curl 请求示例:
``` shell
curl -G 'https://developer.zhihu.com/api/v1/content/global_search' \
  --data-urlencode 'Query=怎么理解rave文化' \
  --data-urlencode 'Filter=host=="example.com" AND publish_time>=1778494631' \
  --data-urlencode 'SearchDB=all' \
  -d 'Count=5' \
  -H 'Authorization: Bearer <your_access_secret>' \
  -H "X-Request-Timestamp: $(date +%s)"
```

Go 语言请求示例:
``` go
package main

import (
    "flag"
    "fmt"
    "io"
    "net/http"
    "net/url"
    "time"
)

const (
    RequestGlobalSearchURL = "https://developer.zhihu.com/api/v1/content/global_search"
)

func main() {
    accessSecret := flag.String("access-secret", "", "Access secret for Bearer authentication")
    query := flag.String("query", "chatgpt", "Search query")
    count := flag.Int("count", 10, "Number of results to return")
    filter := flag.String("filter", "", "Advanced filter expression")
    searchDB := flag.String("search-db", "", "Search index: all, realtime, static")
    flag.Parse()

    response, err := RequestGlobalSearch(*accessSecret, *query, *count, *filter, *searchDB)
    if err != nil {
        fmt.Printf("Failed to request global search: %v\n", err)
        return
    }

    fmt.Printf("response: %+v\n", response)
}

func RequestGlobalSearch(accessSecret string, query string, count int, filter string, searchDB string) (string, error) {
    params := url.Values{}
    params.Set("Query", query)
    params.Set("Count", fmt.Sprintf("%d", count))
    if filter != "" {
        params.Set("Filter", filter)
    }
    if searchDB != "" {
        params.Set("SearchDB", searchDB)
    }

    req, err := http.NewRequest("GET", RequestGlobalSearchURL, nil)
    if err != nil {
        return "", fmt.Errorf("failed to create request: %w", err)
    }

    req.URL.RawQuery = params.Encode()
    req.Header.Set("Authorization", "Bearer "+accessSecret)
    req.Header.Set("X-Request-Timestamp", fmt.Sprintf("%d", time.Now().Unix()))

    client := &http.Client{}
    resp, err := client.Do(req)
    if err != nil {
        return "", fmt.Errorf("failed to send request: %w", err)
    }
    defer func() {
        if err := resp.Body.Close(); err != nil {
            fmt.Printf("Failed to close response body: %v\n", err)
        }
    }()

    body, err := io.ReadAll(resp.Body)
    if err != nil {
        return "", fmt.Errorf("failed to read response: %w", err)
    }

    return string(body), nil
}
```