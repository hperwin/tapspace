import SwiftUI

// Chat-lane motion helpers: a springy press for rail rows and the send
// button, and the presets the chat tab shares.

extension SpringPreset {
    /// A sent bubble lifting out of the composer: quick, lands with a hair of overshoot.
    static let chatFly = SpringPreset(stiffness: 260, damping: 26)
    /// A received bubble settling in.
    static let chatIn = SpringPreset(stiffness: 170, damping: 20)
    /// The composer growing and shrinking with its text.
    static let chatGrow = SpringPreset(stiffness: 340, damping: 25)
}

/// Rows and buttons sink a little under the finger and spring back.
struct ChatPress: ButtonStyle {
    var scale: CGFloat = 0.975

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? scale : 1)
            .animation(.spring(response: 0.24, dampingFraction: 0.6), value: configuration.isPressed)
    }
}
