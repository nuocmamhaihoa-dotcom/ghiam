import SwiftUI

struct ContentView: View {
    @State private var status = "Đang nối hub…"
    @State private var lines: [String] = HubStore.lines
    @State private var base = HubStore.hubBase
    private let timer = Timer.publish(every: 3, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("App này đọc chữ đang hiện trên màn hình. App không bấm vào TikTok hay ứng dụng khác.")
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
    }

    private func saveBase() {
        HubStore.hubBase = base
        base = HubStore.hubBase
        Task { await refresh(connect: true) }
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
