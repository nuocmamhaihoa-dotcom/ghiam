import ReplayKit
import SwiftUI

struct BroadcastPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 320, height: 64))
        picker.preferredExtension = "com.fbpoller.screen.broadcast"
        picker.showsMicrophoneButton = false
        picker.backgroundColor = .black
        picker.layer.cornerRadius = 16
        picker.clipsToBounds = true
        DispatchQueue.main.async {
            guard let button = picker.subviews.compactMap({ $0 as? UIButton }).first else { return }
            button.setTitle("Bắt đầu ghi", for: .normal)
            button.setTitleColor(.white, for: .normal)
            button.titleLabel?.font = .systemFont(ofSize: 20, weight: .heavy)
            button.frame = picker.bounds
            button.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        }
        return picker
    }

    func updateUIView(_ picker: RPSystemBroadcastPickerView, context: Context) {}
}
