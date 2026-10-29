import XCTest

final class ServerSetupUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["--ui-testing-reset-server"]
        app.launch()
    }

    private func enter(_ address: String) {
        let field = app.textFields["serverURL"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        let clear = field.buttons["Clear text"]
        if clear.exists { clear.tap() }
        field.typeText(address)
    }

    private func screenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testFirstLaunchAndInvalidAddress() {
        XCTAssertTrue(app.navigationBars["Connect to CCC"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.webViews.firstMatch.exists)
        screenshot("First launch server setup")
        for address in ["http://example.com", "https://example.com/path", "https://user:password@example.com"] {
            enter(address)
            app.buttons["connectServer"].tap()
            XCTAssertTrue(app.staticTexts["serverURLError"].exists)
            XCTAssertFalse(app.webViews.firstMatch.exists)
        }
        screenshot("Invalid address stays in setup")
    }

    func testOfflineSettingsChangeAndPersistenceAcrossLaunches() {
        enter("https://first.invalid:8443/")
        app.buttons["connectServer"].tap()
        XCTAssertTrue(app.navigationBars.buttons["Settings"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.webViews.staticTexts["Cannot connect to CCC"].waitForExistence(timeout: 30))
        screenshot("Offline recovery has native settings and retry")
        app.navigationBars.buttons["Retry"].tap()
        app.navigationBars.buttons["Settings"].tap()
        XCTAssertEqual(app.textFields["serverURL"].value as? String, "https://first.invalid:8443/")
        enter("https://second.invalid/")
        app.buttons["connectServer"].tap()
        XCTAssertTrue(app.navigationBars.buttons["Settings"].waitForExistence(timeout: 10))
        app.terminate()
        app.launchArguments = []
        app.launch()
        XCTAssertTrue(app.navigationBars.buttons["Settings"].waitForExistence(timeout: 10))
        app.navigationBars.buttons["Settings"].tap()
        XCTAssertEqual(app.textFields["serverURL"].value as? String, "https://second.invalid/")
        screenshot("Saved server after relaunch")
        app.navigationBars.buttons["CCC"].tap()
        XCTAssertTrue(app.navigationBars.buttons["Retry"].exists)
    }
}
