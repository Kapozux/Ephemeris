// 隐私锁用的 Touch ID（2026-10-17，lock.py 调）：直接用 macOS 自己的指纹框（LocalAuthentication，跟 sudo / 1Password 那个一样），
// 不走浏览器的通行密钥（WebAuthn 那个框要点好几下，用户嫌难用）。
//   touchid check            这台 Mac 能不能用指纹：能 → 退出码 0
//   touchid auth "理由"       弹指纹框：按对了 → 0，取消 / 失败 → 1，用不了 → 2。收到 SIGTERM（页面切走了）就收起框、退出 1
// 指纹框里不放「输入密码」：密码在 Ephemeris 的锁屏上输。
import Foundation
import LocalAuthentication

let ctx = LAContext()
ctx.localizedFallbackTitle = ""
var err: NSError?
guard ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &err) else { exit(2) }
let args = CommandLine.arguments
if args.count < 2 || args[1] != "auth" { exit(0) }

signal(SIGTERM, SIG_IGN)
let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
term.setEventHandler { ctx.invalidate(); exit(1) }
term.resume()

ctx.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, localizedReason: args.count > 2 ? args[2] : "解锁 Ephemeris") { ok, _ in
    exit(ok ? 0 : 1)
}
dispatchMain()
