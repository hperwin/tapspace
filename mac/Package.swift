// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TapSpace",
    platforms: [.macOS("26.0")],
    targets: [
        .executableTarget(
            name: "TapSpace",
            path: "Sources/TapSpace",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
