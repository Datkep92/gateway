//  GatewaySwitch — app một nút bật/tắt cổng Gateway (wiki.qaq.gateway).
//  Không dùng HTML: chỉ đọc cờ data/paused và gọi /var/jb/usr/bin/gatewayctl.
#import <UIKit/UIKit.h>
#import <spawn.h>
#import <sys/wait.h>
#import <unistd.h>
#import <string.h>

extern char **environ;

static NSString *const kProjectDir = @"/private/var/mobile/Documents/wiki.qaq.apelin/Projects/Gateway";
static NSString *const kFlagPath = @"/private/var/mobile/Documents/wiki.qaq.apelin/Projects/Gateway/data/paused";
static NSString *const kLogPath = @"/private/var/mobile/Documents/wiki.qaq.apelin/Projects/Gateway/run.log";
static NSString *const kShell = @"/var/jb/bin/sh";
static NSString *const kCtl = @"/var/jb/usr/bin/gatewayctl";
static NSString *const kPathEnv = @"PATH=/var/jb/usr/local/bin:/var/jb/usr/bin:/var/jb/bin:/usr/bin:/bin:/usr/local/bin";

static UIColor *accentOn(void) { return [UIColor colorWithRed:0.24 green:0.86 blue:0.59 alpha:1]; }
static UIColor *accentOff(void) { return [UIColor colorWithRed:1.0 green:0.62 blue:0.45 alpha:1]; }
static UIColor *blueButton(void) { return [UIColor colorWithRed:0.42 green:0.55 blue:1.0 alpha:1]; }
static UIColor *redButton(void) { return [UIColor colorWithRed:1.0 green:0.36 blue:0.48 alpha:1]; }

static BOOL flagExists(void) {
    return access(kFlagPath.UTF8String, F_OK) == 0;
}

/** Chạy một script bằng dash trong /var/jb, gom cả stdout và stderr. */
static NSString *runScript(NSString *script, NSArray<NSString *> *args) {
    int fds[2];
    if (pipe(fds) != 0) {
        return @"Không mở được ống lệnh.";
    }
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, fds[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&fa, fds[1], STDERR_FILENO);
    posix_spawn_file_actions_addclose(&fa, fds[0]);
    posix_spawn_file_actions_addclose(&fa, fds[1]);

    NSMutableArray<NSString *> *list = [NSMutableArray arrayWithObject:kShell];
    [list addObject:script];
    if (args.count) [list addObjectsFromArray:args];
    NSUInteger n = list.count;
    char **cargv = calloc(n + 1, sizeof(char *));
    for (NSUInteger i = 0; i < n; i++) cargv[i] = strdup(list[i].UTF8String);

    char *env[] = { (char *)kPathEnv.UTF8String,
                    (char *)"HOME=/var/mobile",
                    (char *)"LANG=C.UTF-8",
                    NULL };

    pid_t pid = 0;
    int rc = posix_spawn(&pid, kShell.UTF8String, &fa, NULL, cargv, env);
    posix_spawn_file_actions_destroy(&fa);
    close(fds[1]);

    NSMutableData *out = [NSMutableData data];
    if (rc == 0) {
        char buf[4096];
        ssize_t got;
        while ((got = read(fds[0], buf, sizeof buf)) > 0) {
            [out appendBytes:buf length:(NSUInteger)got];
        }
        int status = 0;
        waitpid(pid, &status, 0);
    } else {
        NSString *err = [NSString stringWithFormat:@"Không chạy được %@ (mã %d).\n", script, rc];
        [out appendData:[err dataUsingEncoding:NSUTF8StringEncoding]];
    }
    close(fds[0]);
    for (NSUInteger i = 0; i < n; i++) free(cargv[i]);
    free(cargv);
    NSString *text = [[NSString alloc] initWithData:out encoding:NSUTF8StringEncoding];
    return text ?: @"";
}

/** Đọc phần cuối tệp log để biết địa chỉ và chế độ đang phục vụ. */
static NSString *readLogTail(NSUInteger maxBytes) {
    NSFileHandle *h = [NSFileHandle fileHandleForReadingAtPath:kLogPath];
    if (h == nil) return @"";
    @try {
        unsigned long long size = [h seekToEndOfFile];
        unsigned long long start = size > maxBytes ? size - maxBytes : 0;
        [h seekToFileOffset:start];
        NSData *d = [h readDataToEndOfFile];
        [h closeFile];
        return [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] ?: @"";
    } @catch (__unused NSException *e) {
        return @"";
    }
}

/** "127.0.0.1:15199" lấy từ dòng khởi động gần nhất, hoặc @"" nếu chưa có. */
static NSString *lastAddress(void) {
    NSString *tail = readLogTail(16384);
    if (tail.length == 0) return @"";
    NSString *onMark = @"Gateway starting on ";
    NSString *offMark = @"Gateway paused on ";
    NSRange rOn = [tail rangeOfString:onMark options:NSBackwardsSearch];
    NSRange rOff = [tail rangeOfString:offMark options:NSBackwardsSearch];
    NSRange best = NSMakeRange(NSNotFound, 0);
    NSUInteger markLen = 0;
    if (rOn.location != NSNotFound) {
        best = rOn;
        markLen = onMark.length;
    }
    if (rOff.location != NSNotFound &&
        (rOn.location == NSNotFound || rOff.location > rOn.location)) {
        best = rOff;
        markLen = offMark.length;
    }
    if (best.location == NSNotFound) return @"";
    NSUInteger from = best.location + markLen;
    NSUInteger end = from;
    while (end < tail.length) {
        unichar c = [tail characterAtIndex:end];
        if (c == ' ' || c == '\n' || c == '\r' || c == '(') break;
        end++;
    }
    if (end <= from) return @"";
    return [tail substringWithRange:NSMakeRange(from, end - from)];
}

@interface SwitchVC : UIViewController
@property (nonatomic, strong) UIView *card;
@property (nonatomic, strong) UIImageView *icon;
@property (nonatomic, strong) UILabel *titleLabel;
@property (nonatomic, strong) UILabel *statusLabel;
@property (nonatomic, strong) UILabel *detailLabel;
@property (nonatomic, strong) UIButton *button;
@property (nonatomic, strong) UILabel *msgLabel;
@property (nonatomic, strong) NSTimer *timer;
@property (nonatomic, assign) BOOL busy;
@end

@implementation SwitchVC

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor colorWithRed:0.043 green:0.063 blue:0.125 alpha:1];

    _card = [[UIView alloc] init];
    _card.translatesAutoresizingMaskIntoConstraints = NO;
    _card.backgroundColor = [UIColor colorWithRed:0.082 green:0.114 blue:0.212 alpha:1];
    _card.layer.cornerRadius = 24;
    _card.layer.cornerCurve = kCACornerCurveContinuous;
    _card.layer.borderWidth = 1;
    _card.layer.borderColor = [UIColor colorWithRed:0.145 green:0.188 blue:0.333 alpha:1].CGColor;
    [self.view addSubview:_card];

    _icon = [[UIImageView alloc] initWithImage:[UIImage systemImageNamed:@"power"]];
    _icon.translatesAutoresizingMaskIntoConstraints = NO;
    _icon.contentMode = UIViewContentModeScaleAspectFit;
    [_card addSubview:_icon];

    _titleLabel = [[UILabel alloc] init];
    _titleLabel.translatesAutoresizingMaskIntoConstraints = NO;
    _titleLabel.text = @"Cổng Gateway";
    _titleLabel.font = [UIFont systemFontOfSize:20 weight:UIFontWeightSemibold];
    _titleLabel.textColor = [UIColor colorWithRed:0.906 green:0.925 blue:1.0 alpha:1];
    _titleLabel.textAlignment = NSTextAlignmentCenter;
    [_card addSubview:_titleLabel];

    _statusLabel = [[UILabel alloc] init];
    _statusLabel.translatesAutoresizingMaskIntoConstraints = NO;
    _statusLabel.font = [UIFont systemFontOfSize:15 weight:UIFontWeightBold];
    _statusLabel.textAlignment = NSTextAlignmentCenter;
    [_card addSubview:_statusLabel];

    _detailLabel = [[UILabel alloc] init];
    _detailLabel.translatesAutoresizingMaskIntoConstraints = NO;
    _detailLabel.font = [UIFont systemFontOfSize:13];
    _detailLabel.textColor = [UIColor colorWithRed:0.604 green:0.659 blue:0.808 alpha:1];
    _detailLabel.textAlignment = NSTextAlignmentCenter;
    _detailLabel.numberOfLines = 2;
    [_card addSubview:_detailLabel];

    _button = [UIButton buttonWithType:UIButtonTypeSystem];
    _button.translatesAutoresizingMaskIntoConstraints = NO;
    _button.titleLabel.font = [UIFont systemFontOfSize:17 weight:UIFontWeightSemibold];
    _button.layer.cornerRadius = 16;
    _button.layer.cornerCurve = kCACornerCurveContinuous;
    [_button setTitleColor:[UIColor whiteColor] forState:UIControlStateNormal];
    [_button addTarget:self action:@selector(toggle) forControlEvents:UIControlEventTouchUpInside];
    [_card addSubview:_button];

    _msgLabel = [[UILabel alloc] init];
    _msgLabel.translatesAutoresizingMaskIntoConstraints = NO;
    _msgLabel.font = [UIFont systemFontOfSize:12.5];
    _msgLabel.textColor = [UIColor colorWithRed:0.604 green:0.659 blue:0.808 alpha:1];
    _msgLabel.textAlignment = NSTextAlignmentCenter;
    _msgLabel.numberOfLines = 0;
    [_card addSubview:_msgLabel];

    UILayoutGuide *g = _card.layoutMarginsGuide;
    [NSLayoutConstraint activateConstraints:@[
        [_card.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:24],
        [_card.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-24],
        [_card.centerYAnchor constraintEqualToAnchor:self.view.centerYAnchor constant:-20],

        [_icon.topAnchor constraintEqualToAnchor:g.topAnchor constant:26],
        [_icon.centerXAnchor constraintEqualToAnchor:_card.centerXAnchor],
        [_icon.widthAnchor constraintEqualToConstant:42],
        [_icon.heightAnchor constraintEqualToConstant:42],

        [_titleLabel.topAnchor constraintEqualToAnchor:_icon.bottomAnchor constant:12],
        [_titleLabel.leadingAnchor constraintEqualToAnchor:g.leadingAnchor],
        [_titleLabel.trailingAnchor constraintEqualToAnchor:g.trailingAnchor],

        [_statusLabel.topAnchor constraintEqualToAnchor:_titleLabel.bottomAnchor constant:14],
        [_statusLabel.leadingAnchor constraintEqualToAnchor:g.leadingAnchor],
        [_statusLabel.trailingAnchor constraintEqualToAnchor:g.trailingAnchor],

        [_detailLabel.topAnchor constraintEqualToAnchor:_statusLabel.bottomAnchor constant:6],
        [_detailLabel.leadingAnchor constraintEqualToAnchor:g.leadingAnchor],
        [_detailLabel.trailingAnchor constraintEqualToAnchor:g.trailingAnchor],

        [_button.topAnchor constraintEqualToAnchor:_detailLabel.bottomAnchor constant:20],
        [_button.leadingAnchor constraintEqualToAnchor:g.leadingAnchor],
        [_button.trailingAnchor constraintEqualToAnchor:g.trailingAnchor],
        [_button.heightAnchor constraintEqualToConstant:54],

        [_msgLabel.topAnchor constraintEqualToAnchor:_button.bottomAnchor constant:14],
        [_msgLabel.leadingAnchor constraintEqualToAnchor:g.leadingAnchor],
        [_msgLabel.trailingAnchor constraintEqualToAnchor:g.trailingAnchor],
        [_msgLabel.bottomAnchor constraintEqualToAnchor:g.bottomAnchor constant:-24],
        [_msgLabel.heightAnchor constraintGreaterThanOrEqualToConstant:18],
    ]];

    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(appBecameActive)
                                                 name:UIApplicationDidBecomeActiveNotification
                                               object:nil];
    [self refresh];
}

- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    [self startTimer];
    [self refresh];
}

- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    [self.timer invalidate];
    self.timer = nil;
}

- (void)dealloc {
    [_timer invalidate];
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}

- (void)startTimer {
    if (self.timer) return;
    __weak typeof(self) weakSelf = self;
    self.timer = [NSTimer scheduledTimerWithTimeInterval:2.5 repeats:YES block:^(NSTimer *t) {
        __strong typeof(weakSelf) self2 = weakSelf;
        if (self2 && !self2.busy) [self2 refresh];
    }];
}

- (void)appBecameActive {
    if (!self.busy) [self refresh];
}

- (void)refresh {
    BOOL hasProject = [[NSFileManager defaultManager] fileExistsAtPath:kProjectDir];
    BOOL paused = flagExists();
    NSString *addr = lastAddress();

    if (!hasProject) {
        self.statusLabel.text = @"KHÔNG THẤY DỰ ÁN";
        self.statusLabel.textColor = accentOff();
        self.icon.tintColor = accentOff();
        self.detailLabel.text = @"Chưa có thư mục Gateway trên máy này.";
        [self.button setTitle:@"Không có gì để bật/tắt" forState:UIControlStateNormal];
        self.button.backgroundColor = [UIColor colorWithWhite:0.25 alpha:1];
        self.button.enabled = NO;
        return;
    }

    self.button.enabled = !self.busy;
    self.statusLabel.text = paused ? @"ĐANG TẮT HẲN" : @"ĐANG BẬT";
    self.statusLabel.textColor = paused ? accentOff() : accentOn();
    self.icon.tintColor = paused ? accentOff() : accentOn();
    if (paused) {
        self.detailLabel.text = @"Không chạy gì cả · cổng đã đóng\nkhông tốn pin";
    } else {
        self.detailLabel.text = addr.length
            ? [NSString stringWithFormat:@"%@\nchạy nền · tự chạy lại nếu treo", addr]
            : @"chạy nền · tự chạy lại nếu treo";
    }
    [self.button setTitle:(paused ? @"Mở cổng" : @"Tắt hẳn cổng") forState:UIControlStateNormal];
    self.button.backgroundColor = paused ? blueButton() : redButton();
    if (self.msgLabel.text.length == 0) {
        self.msgLabel.text = @"Chạm một lần là xong, không cần mở trình duyệt.";
    }
}

- (void)toggle {
    if (self.busy) return;
    BOOL paused = flagExists();
    NSString *arg = paused ? @"on" : @"off";
    self.busy = YES;
    self.button.enabled = NO;
    [self.button setTitle:(paused ? @"Đang bật…" : @"Đang tắt…") forState:UIControlStateNormal];
    self.button.backgroundColor = [UIColor colorWithWhite:0.3 alpha:1];
    self.msgLabel.text = paused
        ? @"Đang mở cổng, chờ vài giây…"
        : @"Đang đóng cổng, chờ vài giây…";

    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *out = runScript(kCtl, @[ arg ]);
        dispatch_async(dispatch_get_main_queue(), ^{
            __strong typeof(weakSelf) self2 = weakSelf;
            if (self2 == nil) return;
            self2.busy = NO;
            [self2 refresh];
            NSString *trimmed = [out stringByTrimmingCharactersInSet:
                                 [NSCharacterSet whitespaceAndNewlineCharacterSet]];
            if (trimmed.length == 0) {
                self2.msgLabel.text = paused ? @"Đã gửi lệnh mở cổng." : @"Đã gửi lệnh tắt cổng.";
            } else {
                self2.msgLabel.text = trimmed;
            }
        });
    });
}

@end

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

@implementation AppDelegate

- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary *)options {
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    self.window.rootViewController = [[SwitchVC alloc] init];
    [self.window makeKeyAndVisible];
    return YES;
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([AppDelegate class]));
    }
}