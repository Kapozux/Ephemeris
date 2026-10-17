// Ephemeris 的 Mac App（2026-10-17）：一个原生窗口，里面是 http://localhost:5055 的网页（WKWebView），网页和后台服务都不变。
// 为什么要它：用户想要跟 macOS「密码」App 一样的锁屏 —— 指纹嵌在锁屏里、不弹框。那个控件（LAAuthenticationView）只给原生窗口用，网页拿不到。
//
// 隐私锁（规则在 lock.py；网页那边 static/lock.js 在 App 里不画锁屏，该锁时发消息过来）：
//   · 什么时候锁：切到别的应用、最小化、合盖 / 睡眠 / 锁屏、网页说该锁了（服务器回 423 等）。一锁就把网页整个藏起来（不是糊一层），
//     同时 POST /api/lock/lock 让服务器那头的令牌作废。
//   · 怎么解：App 在前台时指纹自动就绪（嵌在图标右下角，手指放上去就行）；也能输密码。
//     指纹过了 → 拿 lock.APP_KEY_FILE 里的钥匙换令牌（/api/lock/app）；密码 → /api/lock/unlock。令牌放进网页的 cookie。
//     解开那一刻 App 已经不在前台（你切走了）就不解。
// 编译：mac/build.sh（装到 ~/Applications/Ephemeris.app）。
import Cocoa
import LocalAuthentication
import LocalAuthenticationEmbeddedUI
import WebKit

let BASE = URL(string: "http://localhost:5055/")!
let ACCENT = NSColor(srgbRed: 0xD9 / 255, green: 0x77 / 255, blue: 0x57 / 255, alpha: 1)

// ---------------------------------------------------------------- 图标（跟网页左上角那个一样：一圈斜的轨道 + 一颗橙色的星）
func drawMark(in r: NSRect, ink: NSColor) {
    let s = min(r.width, r.height) / 24
    let t = NSAffineTransform()
    t.translateX(by: r.midX, yBy: r.midY)
    t.scale(by: s)
    let ring = NSBezierPath(ovalIn: NSRect(x: -9.4, y: -5.4, width: 18.8, height: 10.8))
    let rot = NSAffineTransform()
    rot.rotate(byDegrees: 32)
    ring.transform(using: rot as AffineTransform)
    ring.transform(using: t as AffineTransform)
    ring.lineWidth = 1.7 * s
    ink.setStroke()
    ring.stroke()
    let dot = NSBezierPath(ovalIn: NSRect(x: 5.4 - 3.1, y: 4.8 - 3.1, width: 6.2, height: 6.2))
    dot.transform(using: t as AffineTransform)
    ACCENT.setFill()
    dot.fill()
}

func appIcon(_ px: CGFloat) -> NSImage {
    let img = NSImage(size: NSSize(width: px, height: px))
    img.lockFocus()
    let tile = NSRect(x: px * 0.1, y: px * 0.1, width: px * 0.8, height: px * 0.8)
    let path = NSBezierPath(roundedRect: tile, xRadius: px * 0.18, yRadius: px * 0.18)
    NSColor(srgbRed: 0.98, green: 0.976, blue: 0.96, alpha: 1).setFill()
    path.fill()
    drawMark(in: tile.insetBy(dx: tile.width * 0.2, dy: tile.width * 0.2), ink: NSColor(srgbRed: 0.08, green: 0.08, blue: 0.075, alpha: 1))
    img.unlockFocus()
    return img
}

final class MarkView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        let tile = bounds.insetBy(dx: 1, dy: 1)
        let path = NSBezierPath(roundedRect: tile, xRadius: 19, yRadius: 19)
        NSColor.controlBackgroundColor.withAlphaComponent(0.92).setFill()
        path.fill()
        NSColor.separatorColor.setStroke()
        path.lineWidth = 1
        path.stroke()
        drawMark(in: tile.insetBy(dx: 17, dy: 17), ink: .labelColor)
    }
}

// ---------------------------------------------------------------- 锁屏（照「密码」App：磨砂、图标右下角嵌着指纹、标题、一句话、一个密码框）
final class LockView: NSVisualEffectView {
    let badge = NSView()
    let field = NSSecureTextField()
    let note = NSTextField(labelWithString: "")
    let sub = NSTextField(labelWithString: "")
    var onPassword: ((String) -> Void)?
    var onBadge: (() -> Void)?

    init() {
        super.init(frame: .zero)
        material = .fullScreenUI
        blendingMode = .behindWindow
        state = .active
        autoresizingMask = [.width, .height]

        let mark = MarkView()
        mark.translatesAutoresizingMaskIntoConstraints = false
        badge.translatesAutoresizingMaskIntoConstraints = false
        badge.addGestureRecognizer(NSClickGestureRecognizer(target: self, action: #selector(badgeClicked)))
        let icon = NSView()
        icon.translatesAutoresizingMaskIntoConstraints = false
        icon.addSubview(mark)
        icon.addSubview(badge)
        NSLayoutConstraint.activate([
            icon.widthAnchor.constraint(equalToConstant: 104), icon.heightAnchor.constraint(equalToConstant: 96),
            mark.widthAnchor.constraint(equalToConstant: 80), mark.heightAnchor.constraint(equalToConstant: 80),
            mark.centerXAnchor.constraint(equalTo: icon.centerXAnchor), mark.topAnchor.constraint(equalTo: icon.topAnchor),
            badge.widthAnchor.constraint(equalToConstant: 44), badge.heightAnchor.constraint(equalToConstant: 44),
            badge.centerXAnchor.constraint(equalTo: mark.trailingAnchor, constant: -4),
            badge.centerYAnchor.constraint(equalTo: mark.bottomAnchor, constant: -4),
        ])

        let title = NSTextField(labelWithString: "Ephemeris 锁着")
        title.font = .systemFont(ofSize: 20, weight: .semibold)
        sub.font = .systemFont(ofSize: 13.5)
        sub.textColor = .secondaryLabelColor
        sub.alignment = .center
        field.placeholderString = "输入密码"
        field.alignment = .center
        field.bezelStyle = .roundedBezel
        field.focusRingType = .default
        field.target = self
        field.action = #selector(submit)
        field.translatesAutoresizingMaskIntoConstraints = false
        field.widthAnchor.constraint(equalToConstant: 220).isActive = true
        note.font = .systemFont(ofSize: 12.5)
        note.textColor = .systemRed
        note.alignment = .center

        let stack = NSStackView(views: [icon, title, sub, field, note])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 8
        stack.setCustomSpacing(18, after: icon)
        stack.setCustomSpacing(16, after: sub)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor, constant: -20),
        ])
    }

    required init?(coder: NSCoder) { fatalError() }

    // 网页藏起来了，点哪儿都落在锁屏上
    override func hitTest(_ point: NSPoint) -> NSView? { super.hitTest(point) ?? self }

    func setTouch(_ view: NSView?) {
        badge.subviews.forEach { $0.removeFromSuperview() }
        sub.stringValue = view == nil ? "输入密码解锁。" : "用 Touch ID 或输入密码解锁。"
        guard let view else { return }
        view.frame = badge.bounds
        view.autoresizingMask = [.width, .height]
        badge.addSubview(view)
    }

    @objc func submit() {
        let pw = field.stringValue
        if pw.isEmpty { onBadge?(); return }            // 空着按回车：再让指纹就绪一次
        onPassword?(pw)
    }

    @objc func badgeClicked() { onBadge?() }
}

// ---------------------------------------------------------------- App
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, NSWindowDelegate {
    var window: NSWindow!
    var web: WKWebView!
    let lockView = LockView()
    var lockOn = false                   // 服务器上设了密码没有
    var locked = false
    var touch: LAContext?

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.applicationIconImage = appIcon(512)
        buildMenu()

        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(self, name: "eph")
        cfg.applicationNameForUserAgent = "Version/18.0 Safari/605.1.15 EphemerisApp/1.0"
        web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.autoresizingMask = [.width, .height]

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Ephemeris"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.tabbingMode = .disallowed
        window.minSize = NSSize(width: 420, height: 520)
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("Ephemeris")
        web.frame = window.contentView!.bounds
        window.contentView!.addSubview(web)
        lockView.frame = window.contentView!.bounds
        lockView.onPassword = { [weak self] pw in self?.unlock(path: "api/lock/unlock", body: ["password": pw]) }
        lockView.onBadge = { [weak self] in self?.armTouch(force: true) }

        let nc = NotificationCenter.default, ws = NSWorkspace.shared.notificationCenter
        nc.addObserver(self, selector: #selector(away), name: NSApplication.didResignActiveNotification, object: nil)
        nc.addObserver(self, selector: #selector(back), name: NSApplication.didBecomeActiveNotification, object: nil)
        nc.addObserver(self, selector: #selector(away), name: NSWindow.didMiniaturizeNotification, object: window)
        for name in [NSWorkspace.screensDidSleepNotification, NSWorkspace.willSleepNotification, NSWorkspace.sessionDidResignActiveNotification] {
            ws.addObserver(self, selector: #selector(away), name: name, object: nil)
        }
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(away), name: .init("com.apple.screenIsLocked"), object: nil)

        // 先问服务器设没设密码：设了就从锁着开始（打开 App = 要解锁）
        status { on in
            self.lockOn = on
            if on { self.lock() }
            self.web.load(URLRequest(url: BASE))
            self.window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    // ---------------- 锁 / 解锁
    @objc func away() { lock() }

    @objc func back() { if locked { armTouch(force: false) } }

    func lock() {
        guard lockOn else { return }
        post("api/lock/lock", [:]) { _, _ in }
        guard !locked else { return }
        locked = true
        web.isHidden = true
        lockView.frame = window.contentView!.bounds
        window.contentView!.addSubview(lockView)
        lockView.field.stringValue = ""
        lockView.note.stringValue = ""
        window.makeFirstResponder(lockView.field)
        if NSApp.isActive { armTouch(force: false) } else { stopTouch() }
    }

    func stopTouch() {
        touch?.invalidate()
        touch = nil
        lockView.setTouch(nil)
        if LAContext().canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil) {
            lockView.sub.stringValue = "用 Touch ID 或输入密码解锁。"
        }
    }

    // 指纹就绪：嵌在图标右下角，不弹框。App 不在前台时不就绪（离开的时候作废）
    func armTouch(force: Bool) {
        guard locked, NSApp.isActive else { return }
        if touch != nil && !force { return }
        touch?.invalidate()
        let ctx = LAContext()
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil) else { lockView.setTouch(nil); return }
        touch = ctx
        lockView.setTouch(LAAuthenticationView(context: ctx, controlSize: .large))
        ctx.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, localizedReason: "解锁 Ephemeris") { ok, err in
            DispatchQueue.main.async {
                guard self.touch === ctx else { return }        // 已经换了一个 / 作废了
                self.touch = nil
                if ok {
                    self.unlock(path: "api/lock/app", body: ["key": self.appKey()])
                } else if let e = err as? LAError, e.code == .authenticationFailed {
                    self.lockView.note.stringValue = "指纹没对上。再按一次，或者输密码"
                    self.armTouch(force: true)
                }
            }
        }
    }

    func appKey() -> String {
        let path = (Bundle.main.object(forInfoDictionaryKey: "EphemerisKeyFile") as? String) ?? ""
        return ((try? String(contentsOfFile: path, encoding: .utf8)) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func unlock(path: String, body: [String: String]) {
        lockView.note.stringValue = ""
        lockView.field.isEnabled = false
        post(path, body) { resp, json in
            self.lockView.field.isEnabled = true
            guard let resp, json["ok"] as? Bool == true else {
                self.lockView.note.stringValue = (json["error"] as? String) ?? "连不上：Ephemeris 还开着吗？"
                self.lockView.field.selectText(nil)
                return
            }
            guard NSApp.isActive else { self.post("api/lock/lock", [:]) { _, _ in }; return }   // 解开那一刻你已经切走了：不解
            let fields = (resp.allHeaderFields as? [String: String]) ?? [:]
            let cookies = HTTPCookie.cookies(withResponseHeaderFields: fields, for: BASE)
            let store = self.web.configuration.websiteDataStore.httpCookieStore
            let group = DispatchGroup()
            for c in cookies { group.enter(); store.setCookie(c) { group.leave() } }
            group.notify(queue: .main) { self.reveal() }
        }
    }

    func reveal() {
        locked = false
        touch?.invalidate()
        touch = nil
        lockView.removeFromSuperview()
        web.isHidden = false
        window.makeFirstResponder(web)
        web.evaluateJavaScript("window.ephLock && ephLock.appUnlocked()")
    }

    // ---------------- 跟服务器说话（不经网页；URLSession 自己的 cookie 不用）
    func post(_ path: String, _ body: [String: String], done: @escaping (HTTPURLResponse?, [String: Any]) -> Void) {
        var req = URLRequest(url: URL(string: path, relativeTo: BASE)!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        req.httpShouldHandleCookies = false
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            let json = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
            DispatchQueue.main.async { done(resp as? HTTPURLResponse, json) }
        }.resume()
    }

    func status(_ done: @escaping (Bool) -> Void) {
        URLSession.shared.dataTask(with: URL(string: "api/lock/status", relativeTo: BASE)!) { data, _, _ in
            let json = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
            DispatchQueue.main.async { done(json["on"] as? Bool ?? false) }
        }.resume()
    }

    // 网页（static/lock.js）说：该锁了 / 设置里刚设好或去掉了密码
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let d = m.body as? [String: Any] else { return }
        if let on = d["on"] as? Bool { lockOn = on; if !on && locked { reveal() } }
        if d["lock"] as? Bool == true { lockOn = true; lock() }
    }

    // ---------------- 网页的那些事：外链用浏览器开、选文件、confirm、服务器还没起来就过一会儿再试
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = a.request.url, let host = url.host, !["localhost", "127.0.0.1"].contains(host) {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for a: WKNavigationAction, windowFeatures f: WKWindowFeatures) -> WKWebView? {
        if let url = a.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ w: WKWebView, runOpenPanelWith p: WKOpenPanelParameters, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = p.allowsMultipleSelection
        panel.canChooseDirectories = p.allowsDirectories
        panel.beginSheetModal(for: window) { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage msg: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = msg
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage msg: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = msg
        a.addButton(withTitle: "好")
        a.addButton(withTitle: "取消")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { w.load(URLRequest(url: BASE)) }   // 后台服务还没起来
    }

    // ---------------- 菜单（没有 Edit 菜单的话，网页和密码框里 ⌘C ⌘V 都不灵）
    func buildMenu() {
        let main = NSMenu()
        func sub(_ title: String, _ items: [NSMenuItem]) {
            let m = NSMenu(title: title)
            items.forEach { m.addItem($0) }
            let it = NSMenuItem()
            it.submenu = m
            main.addItem(it)
        }
        func item(_ t: String, _ a: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k)
            i.keyEquivalentModifierMask = mods
            return i
        }
        sub("Ephemeris", [item("隐藏 Ephemeris", #selector(NSApplication.hide(_:)), "h"), .separator(),
                          item("立刻锁上", #selector(lockNow), "l", [.command, .control]), .separator(),
                          item("退出 Ephemeris", #selector(NSApplication.terminate(_:)), "q")])
        sub("编辑", [item("撤销", Selector(("undo:")), "z"), item("重做", Selector(("redo:")), "z", [.command, .shift]), .separator(),
                    item("剪切", #selector(NSText.cut(_:)), "x"), item("拷贝", #selector(NSText.copy(_:)), "c"),
                    item("粘贴", #selector(NSText.paste(_:)), "v"), item("全选", #selector(NSText.selectAll(_:)), "a")])
        sub("显示", [item("重新载入", #selector(reload), "r"), item("返回", #selector(goBack), "["), item("前进", #selector(goForward), "]")])
        sub("窗口", [item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m"), item("关闭", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = main
    }

    @objc func lockNow() { lock() }
    @objc func reload() { if !locked { web.reload() } }
    @objc func goBack() { if !locked { web.goBack() } }
    @objc func goForward() { if !locked { web.goForward() } }
}

// mac/build.sh 用它画 Finder / Dock 里的图标：Ephemeris --render-icon 输出.png
if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--render-icon" {
    let img = appIcon(1024)
    let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
