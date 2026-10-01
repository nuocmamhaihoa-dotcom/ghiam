import SwiftUI
import UIKit

struct ContentView: View {
    @State private var status = "Đang nối hub…"
    @State private var lines: [String] = HubStore.lines
    @State private var base = HubStore.hubBase
    @State private var scanning = false
    private let timer = Timer.publish(every: 3, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("App đọc chữ hệ thống trên màn hình và lấy @handle từ link / QR. App không bấm vào TikTok hay ứng dụng khác.")
                .font(.system(size: 20))
            Text(status)
                .font(.system(size: 22, weight: .bold))
            TextField("Hub", text: $base)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.system(size: 18))
                .onSubmit { saveBase() }
            BroadcastPicker()
                .frame(maxWidth: .infinity)
                .frame(height: 64)
            HStack(spacing: 12) {
                Button("Dán chữ / link") { Task { await pasteText() } }
                    .buttonStyle(ActionStyle())
                Button("Quét mã QR") { scanning = true }
                    .buttonStyle(ActionStyle())
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        Text(line)
                            .font(.system(size: 18))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.white)
        .task { await refresh(connect: true) }
        .onReceive(timer) { _ in
            Task { await refresh(connect: false) }
        }
        .onOpenURL { url in
            Task { await sendExact(url.absoluteString, source: "share") }
        }
        .sheet(isPresented: $scanning) {
            QRScanner(
                onCode: { value in
                    scanning = false
                    Task { await sendExact(value, source: "share") }
                },
                onCancel: { scanning = false }
            )
            .ignoresSafeArea()
        }
    }

    private func saveBase() {
        HubStore.hubBase = base
        base = HubStore.hubBase
        Task { await refresh(connect: true) }
    }

    private func pasteText() async {
        let text = UIPasteboard.general.string ?? ""
        await sendExact(text, source: "system")
    }

    private func sendExact(_ text: String, source: String) async {
        let collapsed = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        guard !collapsed.isEmpty else {
            status = "Chưa có chữ hoặc link để gửi."
            return
        }
        status = "Đang gửi chữ / link…"
        if HubStore.hubToken.isEmpty {
            status = await HubStore.connect()
        }
        HubStore.remember(collapsed)
        if source == "share" {
            await HubStore.postShare(collapsed)
        } else {
            await HubStore.postLive(collapsed, source: source)
        }
        await refresh(connect: false)
        status = "Đã gửi chữ / link."
    }

    private func refresh(connect: Bool) async {
        if connect {
            status = "Đang nối hub…"
            status = await HubStore.connect()
        }
        if let remote = await HubStore.fetchLines() {
            lines = remote
            HubStore.lines = remote
        } else {
            lines = HubStore.lines
        }
    }
}

private struct ActionStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 18, weight: .heavy))
            .foregroundColor(.black)
            .frame(maxWidth: .infinity, minHeight: 52)
            .background(Color.white)
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.black, lineWidth: 2))
            .opacity(configuration.isPressed ? 0.6 : 1)
    }
}
