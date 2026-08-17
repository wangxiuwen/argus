APP     = Argus
OUT     = .build/$(APP)
BUNDLE  = $(HOME)/Applications/$(APP).app
BIN_DIR = $(HOME)/.local/bin

.PHONY: build install uninstall clean

build:
	mkdir -p .build
	swiftc -O -o $(OUT) Argus/main.swift

install: build
	mkdir -p $(BUNDLE)/Contents/MacOS $(BIN_DIR)
	cp $(OUT) $(BUNDLE)/Contents/MacOS/
	cp Info.plist $(BUNDLE)/Contents/
	codesign --force --sign - $(BUNDLE)
	install -m 755 bin/argus $(BIN_DIR)/argus
	mkdir -p $(HOME)/.local/share/argus
	install -m 644 share/chat.py share/ui.py share/ui.html share/settings.html \
		share/launch.py share/bridge.py $(HOME)/.local/share/argus/
	@echo "installed: $(BUNDLE) and $(BIN_DIR)/argus"
	@echo "open the tray app with: open $(BUNDLE)"

uninstall:
	-$(BIN_DIR)/argus stop
	rm -rf $(BUNDLE)
	rm -f $(BIN_DIR)/argus
	rm -rf $(HOME)/.local/share/argus

clean:
	rm -rf .build
